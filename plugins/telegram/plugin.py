"""Telegram connector + delivery for AUOREO (stdlib + httpx).

Inbound: long-poll `getUpdates(offset, timeout=25)`. A message from a chat that is not on the
allowlist gets a 6-digit pairing code (issued and stored by the host); once the operator runs
`auoreo plugin pair telegram <code>` the chat is allowlisted and its messages become a session.
Outbound: `sendMessage`, chunked at 4000 characters, plain text (no `parse_mode`), with
`message_thread_id` when replying inside a forum topic. When the host sends `meta.citations`
(M7), the answer ends with a plain-text "Sources:" list (trimmed before the answer ever is).
Approvals: `/approve [id|all]`, `/deny [id|all] [reason]` and `/pending` from a paired chat are
forwarded like any message; the host answers them (owner check, FIFO or by request id) in
`auoreo.plugins.chat_approvals` instead of starting a run.

I7 (spec §25.15 "Telegram 10.x"):
- Streaming: with `stream: true` the host sends partial replies (`meta.stream = "partial"`).
  Private chats get `sendMessageDraft` (an animated, ephemeral draft); other chats get one
  message edited in place (`editMessageText`). Both are rate-limited per chat
  (`stream_interval_s`, default 1 s); the final reply persists with `sendMessage` (or a last edit).
- Voice notes: `meta.media` entries (from the host's `MEDIA:` envelope, Opus `.ogg`) go out with
  `sendVoice` (multipart upload, `duration`, caption when the text fits in 1024 characters).
- Token lock: one poller per bot token (`O_EXCL` lock file + PID liveness) so two profiles or
  processes never fight over `getUpdates` (Telegram answers the loser with 409 Conflict).
- Inbound voice (M7, `voice: true`): `message.voice` / `message.audio` from a paired chat is checked
  against the declared length and size, fetched with `getFile`, transcribed by the host
  (`voice.transcribe`) and forwarded as text with `media`; the host prefixes it `[voice] ` and
  never treats it as a command.
- `verify_init_data()`: Mini-App `initData` HMAC-SHA256 check (`WebAppData` key derivation).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl

import httpx

API = "https://api.telegram.org"
CHUNK = 4000
CAPTION_MAX = 1024
SOURCES_MAX = 1500
VOICE_MAX_BYTES = 16 * 2**20
VOICE_IN_MAX_S = 120
VOICE_IN_MAX_BYTES = 5 * 2**20
NAME = "telegram"


class TelegramError(RuntimeError):
    pass


def verify_init_data(
    init_data: str, bot_token: str, *, max_age_s: float = 86400, now: float | None = None
) -> dict[str, Any]:
    """Validate a Mini-App `initData` query string; returns its fields (`user` JSON-decoded).

    secret = HMAC_SHA256(key="WebAppData", msg=bot_token); the `hash` field must equal
    hex(HMAC_SHA256(key=secret, msg=data_check_string)) where data_check_string is every other
    `key=value` pair sorted by key and joined with newlines. An `auth_date` older than
    `max_age_s` is rejected (replay)."""
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    given = pairs.pop("hash", "")
    if not given:
        raise TelegramError("initData has no hash")
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, given.lower()):
        raise TelegramError("initData hash mismatch")
    try:
        auth_date = float(pairs.get("auth_date", "0"))
    except ValueError as exc:
        raise TelegramError("initData auth_date is not a number") from exc
    if max_age_s and (now if now is not None else time.time()) - auth_date > max_age_s:
        raise TelegramError("initData is too old")
    out: dict[str, Any] = dict(pairs)
    if "user" in out:
        with contextlib.suppress(ValueError):
            out["user"] = json.loads(out["user"])
    return out


class TokenLock:
    """One process per bot token: `<dir>/auoreo-telegram-<sha256(token)[:16]>.lock` created with
    O_EXCL; a lock whose PID is gone is stale and taken over."""

    def __init__(self, token: str, directory: str | None = None) -> None:
        digest = hashlib.sha256(token.encode()).hexdigest()[:16]
        self.path = Path(directory or tempfile.gettempdir()) / f"auoreo-telegram-{digest}.lock"
        self.held = False

    @staticmethod
    def alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def holder(self) -> int | None:
        try:
            return int(self.path.read_text().strip() or 0)
        except (OSError, ValueError):
            return None

    def acquire(self) -> bool:
        if self.held:
            return True
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pid = self.holder()
                if pid is not None and pid != os.getpid() and self.alive(pid):
                    return False
                with contextlib.suppress(FileNotFoundError):
                    self.path.unlink()  # stale (dead PID) or our own leftover
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(str(os.getpid()))
            self.held = True
            return True
        return False

    def release(self) -> None:
        if self.held and self.holder() == os.getpid():
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
        self.held = False


def sources_block(citations: Any, limit: int = SOURCES_MAX) -> str:
    """`Sources:` + one numbered plain-text line per citation (`title — url`), at most `limit`
    characters: trailing entries are dropped to fit. Titles are untrusted, so each is flattened
    to one line; the tool call id is not shown. "" when there is nothing to list."""
    lines: list[str] = []
    for item in citations if isinstance(citations, list) else ():
        url = item.get("url") if isinstance(item, dict) else None
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or not url.isprintable():
            continue
        raw = item.get("title") if isinstance(item.get("title"), str) else ""
        title = " ".join("".join(ch if ch.isprintable() else " " for ch in raw).split())[:120]
        line = f"{len(lines) + 1}. {title} — {url}" if title else f"{len(lines) + 1}. {url}"
        if len("Sources:") + sum(len(x) + 1 for x in lines) + len(line) + 1 > limit:
            break
        lines.append(line)
    return "\n".join(["Sources:", *lines]) if lines else ""


def with_sources(parts: list[str], block: str, size: int = CHUNK) -> list[str]:
    """Put the block after the answer: in the last message when it fits, else in its own."""
    if not block:
        return parts
    if len(parts[-1]) + 2 + len(block) <= size:
        return [*parts[:-1], f"{parts[-1]}\n\n{block}"]
    return [*parts, block]


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
        logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO line quotes the token URL
        self.host = host
        self.transport = transport
        self.config: dict[str, Any] = {}
        self.allowlist: set[str] = set()
        self.offset = 0
        self._token: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._poll: asyncio.Task[None] | None = None
        self.lock: TokenLock | None = None
        self.streams: dict[str, dict[str, Any]] = {}  # chat_id → {mode, draft_id, message_id, at, text}
        self._draft_ids = 0

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
            token = await self._bot_token()
            self.lock = self.lock or TokenLock(token, self.config.get("lock_dir"))
            if not self.lock.acquire():
                msg = (
                    f"telegram: another process (pid {self.lock.holder()}) already polls this bot "
                    "token; not polling here (one poller per token)"
                )
                self.host.log("error", msg)
                with contextlib.suppress(Exception):
                    await self.host.audit("token_locked", {"holder_pid": self.lock.holder()})
                return {"ok": False, "detail": msg}
            self._poll = asyncio.create_task(self._poll_loop())
        return {}

    async def connector_stop(self, name: str) -> dict[str, Any]:
        if self._poll is not None:
            self._poll.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll
            self._poll = None
        if self.lock is not None:
            self.lock.release()
        return {}

    async def shutdown(self) -> None:
        await self.connector_stop(NAME)
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ API
    async def _bot_token(self) -> str:
        if self._token is None:
            self._token = await self.host.secret("telegram/bot_token")
            if not self._token:
                raise TelegramError("secret telegram/bot_token is not set")
        return self._token

    async def _api(self, method: str, payload: dict[str, Any], files: dict[str, Any] | None = None) -> Any:
        token = await self._bot_token()
        if self._client is None:
            timeout = httpx.Timeout(10.0, read=float(self.config.get("poll_timeout_s", 25)) + 15)
            self._client = httpx.AsyncClient(transport=self.transport, timeout=timeout)
        url = f"{API}/bot{token}/{method}"
        if files:
            resp = await self._client.post(url, data={k: str(v) for k, v in payload.items()}, files=files)
        else:
            resp = await self._client.post(url, json=payload)
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
        try:
            if meta.get("stream") == "partial":
                return await self.stream_partial(str(target), text, thread_id)
            return await self._send_final(str(target), text, meta, thread_id)
        except (TelegramError, httpx.HTTPError, OSError) as exc:
            return {"ok": False, "detail": str(exc), "external_id": None}

    async def _send_final(
        self, target: str, text: str, meta: dict[str, Any], thread_id: Any
    ) -> dict[str, Any]:
        media = [m for m in meta.get("media") or [] if isinstance(m, dict) and m.get("path")]
        last: Any = None
        sent = 0
        caption = text if media and 0 < len(text) <= CAPTION_MAX else ""
        for i, item in enumerate(media):
            last = await self.send_voice(target, item, caption if i == len(media) - 1 else "", thread_id)
            sent += 1
        stream = self.streams.pop(target, None)
        if not media or (text and not caption):
            parts = with_sources(chunk(text or "(empty)"), sources_block(meta.get("citations")))
            if stream and stream["mode"] == "edit" and stream.get("message_id") is not None:
                payload = {"chat_id": target, "message_id": stream["message_id"], "text": parts.pop(0)}
                last = await self._api("editMessageText", payload)
                sent += 1
            for part in parts:
                body: dict[str, Any] = {"chat_id": target, "text": part}
                if thread_id is not None:
                    body["message_thread_id"] = int(thread_id)
                last = await self._api("sendMessage", body)
                sent += 1
        msg_id = last.get("message_id") if isinstance(last, dict) else None
        return {"ok": True, "detail": f"{sent} message(s)", "external_id": msg_id}

    async def send_voice(
        self, chat_id: str, item: dict[str, Any], caption: str, thread_id: Any = None
    ) -> Any:
        """`sendVoice` multipart upload of an Opus `.ogg` voice note (16 MiB cap)."""
        path = Path(str(item["path"]))
        size = (await asyncio.to_thread(path.stat)).st_size
        if size > VOICE_MAX_BYTES:
            raise TelegramError(f"voice note is {size} bytes; the cap is {VOICE_MAX_BYTES}")
        payload: dict[str, Any] = {"chat_id": chat_id}
        if item.get("duration_s") is not None:
            payload["duration"] = max(1, round(float(item["duration_s"])))
        if caption:
            payload["caption"] = caption
        if thread_id is not None:
            payload["message_thread_id"] = int(thread_id)
        data = await asyncio.to_thread(path.read_bytes)
        files = {"voice": (path.name, data, str(item.get("mime") or "audio/ogg"))}
        return await self._api("sendVoice", payload, files=files)

    # ----------------------------------------------------------- streaming
    def _stream_mode(self, chat_id: str) -> str:
        mode = str(self.config.get("stream_mode", "auto"))
        if mode != "auto":
            return mode
        return "draft" if chat_id.isdigit() else "edit"  # private chats have positive ids

    async def stream_partial(self, chat_id: str, text: str, thread_id: Any = None) -> dict[str, Any]:
        """Show the reply so far: a draft (private chats) or one message edited in place."""
        now = time.monotonic()
        st = self.streams.get(chat_id)
        if st is None:
            self._draft_ids += 1
            st = {"mode": self._stream_mode(chat_id), "draft_id": self._draft_ids, "message_id": None}
            st.update(at=-1e9, text="")
            self.streams[chat_id] = st
        if now - st["at"] < float(self.config.get("stream_interval_s", 1.0)):
            return {"ok": True, "detail": "rate-limited", "external_id": None}
        view = text if len(text) <= CHUNK else text[: CHUNK - 1] + "…"
        if not view.strip() or view == st["text"]:
            return {"ok": True, "detail": "unchanged", "external_id": None}
        payload: dict[str, Any] = {"chat_id": chat_id, "text": view}
        if thread_id is not None:
            payload["message_thread_id"] = int(thread_id)
        if st["mode"] == "draft":
            await self._api("sendMessageDraft", payload | {"draft_id": st["draft_id"]})
        elif st["message_id"] is None:
            res = await self._api("sendMessage", payload)
            st["message_id"] = res.get("message_id") if isinstance(res, dict) else None
        else:
            edit = {"chat_id": chat_id, "message_id": st["message_id"], "text": view}
            await self._api("editMessageText", edit)
        st["at"], st["text"] = now, view
        return {"ok": True, "detail": f"stream {st['mode']}", "external_id": st["message_id"]}

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
        if not isinstance(msg, dict):
            return
        note = (msg.get("voice") or msg.get("audio")) if self.config.get("voice") else None
        if not msg.get("text") and not isinstance(note, dict):
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
        if not msg.get("text") and isinstance(note, dict):
            await self.voice_note(chat_id, user, note, reply_to)
            return
        self.host.message(NAME, chat_id, user, str(msg["text"]), reply_to=reply_to)

    # ---------------------------------------------------------------- voice
    async def voice_note(self, chat_id: str, user: str, note: dict[str, Any], reply_to: Any) -> None:
        """M7: check the declared length and size, download through `getFile`, ask the host to
        transcribe (`voice.transcribe`, quotas host side), forward the text with `media`. The
        download URL holds the bot token: it is never logged and errors never quote it."""
        max_s = int(self.config.get("voice_max_seconds", VOICE_IN_MAX_S))
        max_b = int(self.config.get("voice_max_bytes", VOICE_IN_MAX_BYTES))
        duration, size = int(note.get("duration") or 0), int(note.get("file_size") or 0)
        mime = str(note.get("mime_type") or "audio/ogg")

        async def say(text: str) -> None:
            await self.send(NAME, chat_id, text, {"reply_to": reply_to})

        if duration > max_s:
            return await say(f"Voice note is {duration} s; the limit is {max_s} s.")
        if size > max_b:
            return await say(f"Voice note is {size} bytes; the limit is {max_b} bytes.")
        transcribe = getattr(self.host, "voice_transcribe", None)
        if transcribe is None:
            return await say("Voice notes are not supported by this AUOREO host.")
        try:
            data = await self._download(str(note.get("file_id", "")), max_b)
        except (TelegramError, httpx.HTTPError, OSError, KeyError, TypeError):
            return await say("The voice note could not be downloaded.")
        try:
            res = await transcribe(chat_id, mime, data)
        except Exception as exc:
            if getattr(exc, "code", None) == -32601:
                return await say("Voice notes are not supported by this AUOREO host.")
            return await say(f"Voice note not transcribed: {str(exc)[:200]}")
        media = [{"kind": "voice", "mime": mime, "duration_s": res.get("duration_s")}]
        self.host.message(NAME, chat_id, user, str(res.get("text", "")), reply_to=reply_to, media=media)

    async def _download(self, file_id: str, max_bytes: int) -> bytes:
        info = await self._api("getFile", {"file_id": file_id})
        path = str(info["file_path"])
        if int(info.get("file_size") or 0) > max_bytes:
            raise TelegramError("file too large")
        token = await self._bot_token()
        assert self._client is not None  # created by `_api`
        resp = await self._client.get(f"{API}/file/bot{token}/{path}")
        if resp.status_code != 200 or len(resp.content) > max_bytes:
            raise TelegramError(f"download: HTTP {resp.status_code}")
        return resp.content
