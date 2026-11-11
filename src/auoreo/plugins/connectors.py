"""Connector plumbing shared by every plugin: pairing codes, the chat allowlist and the
`(plugin, chat_id) → session` map. Inbound text runs non-interactively in that session and the
final answer goes back through the plugin's delivery.

State lives in files under `<home>/plugins/<name>/` (and the allowlist in the settings store) so
`auoreo plugin pair` works from another process while the server is running.

`/approve`, `/deny [reason]`, `/approve all` and `/pending` from a paired owner chat answer
pending approvals instead of starting a run (`plugins.chat_approvals`, spec §19.9).

M7: a connector config with `routes` picks the agent per message (`plugins.routes`); a routed
agent gets its own session under the key `<chat_id>:<agent>` and each decision is audited as
`inbound.routed`. Without `routes` nothing here changes. With `m7.agents.citations` on, the
reply meta carries `citations` built from the run's tool events (`plugins.citations`).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auoreo.core.settings import write_settings_value

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
CODE_TTL_S = 3600
Send = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]
_LOCKS: dict[str, asyncio.Lock] = {}
"""One lock per `sessions.json`: the host builds a router per message."""


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


VOICE_PREFIX = "[voice] "


def is_voice(params: dict[str, Any]) -> bool:
    """M7: `media` (additive, metadata only) names a voice note the host transcribed."""
    media = params.get("media")
    return isinstance(media, list) and any(isinstance(m, dict) and m.get("kind") == "voice" for m in media)


def file_config(home: Path, plugin: str) -> dict[str, Any]:
    """`plugins.<name>` from the settings store (state.db), read fresh (pairing edits it at runtime)."""
    from auoreo.core import settings_store

    try:
        node = settings_store.read(home).get("plugins", {})
    except settings_store.SettingsStoreError:
        node = {}
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

    def revoke(self, chat_id: str) -> bool:
        """--- I5: drop `chat_id` from the settings allowlist (`auoreo pairing revoke`)."""
        listed = file_config(self.home, self.plugin).get("allowlist", [])
        allow = [str(x) for x in listed] if isinstance(listed, list) else []
        if chat_id not in allow:
            return False
        write_settings_value(
            self.home, f"plugins.{self.plugin}.allowlist", [c for c in allow if c != chat_id]
        )
        return True


@dataclass(frozen=True)
class Routed:
    """One routing decision. Without `routes` only `agent` is set and nothing else changes."""

    agent: str | None  # bound when the session is created (None = the default agent)
    key: str | None  # sessions.json key; None = the bare chat id
    why: str = ""  # "" = the connector has no routes (no audit row)
    named: str = ""  # the agent's name for the audit row and approval prompts
    routed: bool = False  # a rule picked an agent other than the connector's own

    @property
    def label(self) -> str:
        return f"[{self.named}] " if self.routed else ""

    async def audit(self, services: Services, plugin: str, chat_id: str, sid: str) -> None:
        if not self.why:
            return
        await services.audit.record(
            actor="system",
            action="inbound.routed",
            session_id=sid,
            decision="allow",
            reason=self.why,
            detail={"plugin": plugin, "chat_id": chat_id[:64], "agent": self.named},
        )


class InboundRouter:
    """`connector.message` → session (created on first contact) → reply via delivery."""

    def __init__(self, services: Services, plugin: str, *, timeout_s: float = 900.0) -> None:
        self.s = services
        self.plugin = plugin
        self.timeout_s = timeout_s
        self.path = services.settings.home / "plugins" / plugin / "sessions.json"

    def _config(self) -> dict[str, Any]:
        return {
            **self.s.settings.plugins.get(self.plugin, {}),
            **file_config(self.s.settings.home, self.plugin),
        }

    async def session_for(self, chat_id: str, user: str, agent: str | None, key: str | None = None) -> str:
        """The session of `key` (default: the chat id), created with `agent` on first contact.
        Serialized per plugin: the host runs one task per message."""
        key = key or chat_id
        async with _LOCKS.setdefault(str(self.path), asyncio.Lock()):
            return await self._session_for(chat_id, user, agent, key)

    async def _session_for(self, chat_id: str, user: str, agent: str | None, key: str) -> str:
        mapping = _read_json(self.path)
        sid = mapping.get(key)
        if sid and await self.s.get_session(sid) is not None:
            return str(sid)
        row = await self.s.create_session(title=f"{self.plugin}: {user or chat_id}"[:120], agent=agent)
        mapping[key] = row.id
        _write_json(self.path, mapping)
        await self.s.emit(
            "server",
            "plugin.session_created",
            {"plugin": self.plugin, "chat_id": chat_id, "session_id": row.id},
        )
        return row.id

    async def _route(self, params: dict[str, Any], chat_id: str, fixed: str | None) -> Routed:
        """Which agent answers: the connector's fixed one, or the first matching route."""
        cfg = self._config()
        if not cfg.get("routes"):
            return Routed(fixed, None)
        from auoreo.plugins import routes

        payload, owner = routes.payload_of(params), routes.is_owner(cfg, chat_id)
        pick = routes.select(cfg["routes"], payload, owner=owner, agents=self.s.harness.agents)
        default = fixed or self.s.settings.agent
        if pick.agent is None or pick.agent == default:
            return Routed(fixed, None, pick.why, default)
        return Routed(pick.agent, routes.session_key(chat_id, pick.agent), pick.why, pick.agent, True)

    async def handle(self, params: dict[str, Any], send: Send, agent: str | None = None) -> dict[str, Any]:
        chat_id, text = str(params.get("chat_id", "")), str(params.get("text", ""))
        if not chat_id or not text.strip():
            return {"ok": False, "detail": "empty message"}
        from auoreo.plugins import consent
        from auoreo.plugins.chat_approvals import ApprovalNotifier, handle_command, is_command

        meta = {"reply_to": params.get("reply_to"), "connector": params.get("name")}
        public = consent.reply_mode(self.s, self.plugin)  # --- M7: replies become suggestions
        if is_voice(params):  # --- M7: a transcript is never a chat or engine command
            text = f"{VOICE_PREFIX}{text}"
            params = {**params, "text": text}
        elif is_command(text) and not public:
            answer = await handle_command(self.s, self.plugin, chat_id, text)
            receipt = await send(chat_id, answer, meta)
            return {"ok": bool(receipt.get("ok")), "command": True, "detail": receipt.get("detail")}
        route = await self._route(params, chat_id, agent)  # --- M7: `routes` in the connector config
        sid = await self.session_for(chat_id, str(params.get("user", "")), route.agent, route.key)
        await route.audit(self.s, self.plugin, chat_id, sid)
        interactive = bool(self.s.settings.teams2.chat_approvals) and not public
        from auoreo.plugins import citations

        seq = await citations.mark(self.s)  # --- M7: None unless `m7.agents.citations` is on
        run_id = await self.s.coordinator.submit(sid, text, interactive=interactive)
        reply, cite, draft = "(done)", {}, ""
        if run_id is not None:
            async with contextlib.AsyncExitStack() as stack:
                if interactive:
                    notifier = ApprovalNotifier(self.s, sid, chat_id, send, agent=route.named)
                    await stack.enter_async_context(notifier)
                if self._config().get("stream") and not public:  # --- I7: partial replies (drafts / edits)
                    from auoreo.plugins.streaming import StreamRelay

                    relay = StreamRelay(self.s, sid, chat_id, send, {**meta, "session_id": sid})
                    await stack.enter_async_context(relay)
                await self.s.coordinator.wait(sid, self.timeout_s)
            result = self.s.coordinator.result(run_id)
            if found := await citations.for_run(self.s, sid, run_id, seq, result):
                cite = {"citations": found}
            if result is None:
                reply = "(no answer)"
            elif result.status in {"completed", "max_steps"}:
                reply, draft = result.text or "(empty answer)", result.text or ""
            else:
                reply = f"(run {result.status}: {(result.error or {}).get('message', '')})".strip()
        if public:
            return await consent.propose_reply(self.s, self.plugin, params, draft, sid) | {"run_id": run_id}
        from auoreo.automation.deliveries import voice_notes  # --- I7: MEDIA: envelope

        reply, extra = await voice_notes(self.s, self.plugin, reply)
        receipt = await send(chat_id, route.label + reply, {**meta, "session_id": sid, **extra, **cite})
        return {
            "ok": bool(receipt.get("ok")),
            "session_id": sid,
            "run_id": run_id,
            "detail": receipt.get("detail"),
        }
