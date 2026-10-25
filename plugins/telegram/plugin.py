"""Telegram connector + delivery for AUOREO (stdlib + httpx).

Inbound: long-poll `getUpdates(offset, timeout=25)`. A message from a chat that is not on the
allowlist gets a 6-digit pairing code (issued and stored by the host); once the operator runs
`auoreo plugin pair telegram <code>` the chat is allowlisted and its messages become a session.
Outbound: `sendMessage`, chunked at 4000 characters, plain text (no `parse_mode`), with
`message_thread_id` when replying inside a forum topic.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx

API = "https://api.telegram.org"
CHUNK = 4000
NAME = "telegram"


class TelegramError(RuntimeError):
    pass


def chunk(text: str, size: int = CHUNK) -> list[str]:
    """Split on newlines (then spaces) so no piece exceeds `size`; never returns []."""
    out: list[str] = []
    rest = text
    while len(rest) > size:
        cut = rest.rfind("\n", 0, size)
        if cut <= 0:
            cut = rest.rfind(" ", 0, size)
        if cut <= 0:
            cut = size
        out.append(rest[:cut])
        rest = rest[cut:].lstrip("\n ") if cut < len(rest) else ""
    if rest or not out:
        out.append(rest)
    return out


class Plugin:
    def __init__(self, host: Any, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.host = host
        self.transport = transport
        self.config: dict[str, Any] = {}
        self.allowlist: set[str] = set()
        self.offset = 0
        self._token: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._poll: asyncio.Task[None] | None = None

    # ------------------------------------------------------------- protocol
    def initialize(self, config: dict[str, Any], consumes_granted: list[str]) -> dict[str, Any]:
        self.config_update(config)
        return {"tools": [], "deliveries": [NAME], "connectors": [NAME], "skills": []}

    def config_update(self, config: dict[str, Any]) -> dict[str, Any]:
        self.config = dict(config)
        self.allowlist = {str(c) for c in config.get("allowlist", [])}
        return {}

    async def connector_start(self, name: str) -> dict[str, Any]:
        if self._poll is None or self._poll.done():
            self._poll = asyncio.create_task(self._poll_loop())
        return {}

    async def connector_stop(self, name: str) -> dict[str, Any]:
        if self._poll is not None:
            self._poll.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll
            self._poll = None
        return {}

    async def shutdown(self) -> None:
        await self.connector_stop(NAME)
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ API
    async def _api(self, method: str, payload: dict[str, Any]) -> Any:
        if self._token is None:
            self._token = await self.host.secret("telegram/bot_token")
            if not self._token:
                raise TelegramError("secret telegram/bot_token is not set")
        if self._client is None:
            timeout = httpx.Timeout(10.0, read=float(self.config.get("poll_timeout_s", 25)) + 15)
            self._client = httpx.AsyncClient(transport=self.transport, timeout=timeout)
        resp = await self._client.post(f"{API}/bot{self._token}/{method}", json=payload)
        try:
            data = resp.json()
        except ValueError as exc:
            raise TelegramError(f"{method}: HTTP {resp.status_code}") from exc
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description', resp.status_code)}")
        return data.get("result")

    async def send(self, name: str, target: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
        reply_to = meta.get("reply_to") if isinstance(meta.get("reply_to"), dict) else {}
        thread_id = reply_to.get("thread_id") if reply_to else None
        last: Any = None
        parts = chunk(text or "(empty)")
        try:
            for part in parts:
                payload: dict[str, Any] = {"chat_id": target, "text": part}
                if thread_id is not None:
                    payload["message_thread_id"] = int(thread_id)
                last = await self._api("sendMessage", payload)
        except (TelegramError, httpx.HTTPError) as exc:
            return {"ok": False, "detail": str(exc), "external_id": None}
        msg_id = last.get("message_id") if isinstance(last, dict) else None
        return {"ok": True, "detail": f"{len(parts)} message(s)", "external_id": msg_id}

    # ------------------------------------------------------------- inbound
    async def poll_once(self) -> int:
        timeout = int(self.config.get("poll_timeout_s", 25))
        payload = {"offset": self.offset, "timeout": timeout, "allowed_updates": ["message"]}
        updates = await self._api("getUpdates", payload) or []
        for update in updates:
            self.offset = max(self.offset, int(update["update_id"]) + 1)
            await self.handle_update(update)
        return len(updates)

    async def _poll_loop(self) -> None:
        backoff = 1.0
        while True:
            try:
                if not await self.poll_once():
                    await asyncio.sleep(0.05)  # a server that answers empty polls instantly
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.host.log("warning", f"telegram poll failed: {exc}")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def handle_update(self, update: dict[str, Any]) -> None:
        msg = update.get("message")
        if not isinstance(msg, dict) or not msg.get("text"):
            return
        chat_id = str(msg["chat"]["id"])
        sender = msg.get("from") or {}
        user = str(sender.get("username") or sender.get("first_name") or chat_id)
        reply_to: dict[str, Any] = {"message_id": msg.get("message_id")}
        if msg.get("is_topic_message") and msg.get("message_thread_id") is not None:
            reply_to["thread_id"] = msg["message_thread_id"]
        if chat_id not in self.allowlist:
            res = await self.host.pairing_request(NAME, chat_id, user)
            if not res.get("allowed"):
                code = res.get("code", "")
                text = (
                    f"This chat is not paired with AUOREO yet. Pairing code: {code}\n"
                    f"On the AUOREO host run: auoreo plugin pair telegram {code}"
                )
                await self.send(NAME, chat_id, text, {"reply_to": reply_to})
                return
            self.allowlist.add(chat_id)
        self.host.message(NAME, chat_id, user, str(msg["text"]), reply_to=reply_to)
