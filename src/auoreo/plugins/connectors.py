"""Connector plumbing shared by every plugin: pairing codes, the chat allowlist and the
`(plugin, chat_id) → session` map. Inbound text runs non-interactively in that session and the
final answer goes back through the plugin's delivery.

State lives in files under `<home>/plugins/<name>/` (and the allowlist in `settings.json`) so
`auoreo plugin pair` works from another process while the server is running.

`/approve`, `/deny [reason]`, `/approve all` and `/pending` from a paired owner chat answer
pending approvals instead of starting a run (`plugins.chat_approvals`, spec §19.9).
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auoreo.core.settings import write_settings_value

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
CODE_TTL_S = 3600
Send = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def file_config(home: Path, plugin: str) -> dict[str, Any]:
    """`plugins.<name>` from `<home>/settings.json`, read fresh (pairing edits it at runtime)."""
    node = _read_json(home / "settings.json").get("plugins", {})
    cfg = node.get(plugin, {}) if isinstance(node, dict) else {}
    return cfg if isinstance(cfg, dict) else {}


class PairingStore:
    """6-digit codes for unknown chats; `pair(code)` adds the chat to the allowlist."""

    def __init__(self, home: Path, plugin: str) -> None:
        self.home = home
        self.plugin = plugin
        self.path = home / "plugins" / plugin / "pairing.json"

    def allowlist(self, extra: list[str] | None = None) -> list[str]:
        listed = file_config(self.home, self.plugin).get("allowlist", [])
        merged = [str(x) for x in [*(extra or []), *(listed if isinstance(listed, list) else [])]]
        return list(dict.fromkeys(merged))

    def _codes(self) -> dict[str, dict[str, Any]]:
        now = time.time()
        codes = _read_json(self.path).get("codes", {})
        return {c: v for c, v in codes.items() if now - float(v.get("created", 0)) < CODE_TTL_S}

    def request(self, chat_id: str, user: str, connector: str = "") -> str:
        """The pending code for `chat_id` (reused while valid) or a fresh one."""
        codes = self._codes()
        for code, v in codes.items():
            if v.get("chat_id") == chat_id:
                return code
        code = f"{secrets.randbelow(1_000_000):06d}"
        while code in codes:
            code = f"{secrets.randbelow(1_000_000):06d}"
        codes[code] = {"chat_id": chat_id, "user": user, "connector": connector, "created": time.time()}
        _write_json(self.path, {"codes": codes})
        return code

    def pending(self) -> list[dict[str, Any]]:
        return [{"chat_id": v["chat_id"], "user": v.get("user", "")} for v in self._codes().values()]

    def pair(self, code: str) -> str | None:
        """Consume `code`; returns the newly allowed chat id (None for an unknown/expired code)."""
        codes = self._codes()
        entry = codes.pop(code.strip(), None)
        if entry is None:
            return None
        chat_id = str(entry["chat_id"])
        allow = self.allowlist()
        if chat_id not in allow:
            write_settings_value(self.home, f"plugins.{self.plugin}.allowlist", [*allow, chat_id])
        _write_json(self.path, {"codes": codes})
        return chat_id


class InboundRouter:
    """`connector.message` → session (created on first contact) → reply via delivery."""

    def __init__(self, services: Services, plugin: str, *, timeout_s: float = 900.0) -> None:
        self.s = services
        self.plugin = plugin
        self.timeout_s = timeout_s
        self.path = services.settings.home / "plugins" / plugin / "sessions.json"

    async def session_for(self, chat_id: str, user: str, agent: str | None) -> str:
        mapping = _read_json(self.path)
        sid = mapping.get(chat_id)
        if sid and await self.s.get_session(sid) is not None:
            return str(sid)
        row = await self.s.create_session(title=f"{self.plugin}: {user or chat_id}"[:120], agent=agent)
        mapping[chat_id] = row.id
        _write_json(self.path, mapping)
        await self.s.emit(
            "server",
            "plugin.session_created",
            {"plugin": self.plugin, "chat_id": chat_id, "session_id": row.id},
        )
        return row.id

    async def handle(self, params: dict[str, Any], send: Send, agent: str | None = None) -> dict[str, Any]:
        chat_id, text = str(params.get("chat_id", "")), str(params.get("text", ""))
        if not chat_id or not text.strip():
            return {"ok": False, "detail": "empty message"}
        from auoreo.plugins.chat_approvals import ApprovalNotifier, handle_command, is_command

        meta = {"reply_to": params.get("reply_to"), "connector": params.get("name")}
        if is_command(text):
            answer = await handle_command(self.s, self.plugin, chat_id, text)
            receipt = await send(chat_id, answer, meta)
            return {"ok": bool(receipt.get("ok")), "command": True, "detail": receipt.get("detail")}
        sid = await self.session_for(chat_id, str(params.get("user", "")), agent)
        interactive = bool(self.s.settings.teams2.chat_approvals)
        run_id = await self.s.coordinator.submit(sid, text, interactive=interactive)
        reply = "(done)"
        if run_id is not None:
            if interactive:
                async with ApprovalNotifier(self.s, sid, chat_id, send):
                    await self.s.coordinator.wait(sid, self.timeout_s)
            else:
                await self.s.coordinator.wait(sid, self.timeout_s)
            result = self.s.coordinator.result(run_id)
            if result is None:
                reply = "(no answer)"
            elif result.status in {"completed", "max_steps"}:
                reply = result.text or "(empty answer)"
            else:
                reply = f"(run {result.status}: {(result.error or {}).get('message', '')})".strip()
        receipt = await send(chat_id, reply, {**meta, "session_id": sid})
        return {
            "ok": bool(receipt.get("ok")),
            "session_id": sid,
            "run_id": run_id,
            "detail": receipt.get("detail"),
        }
