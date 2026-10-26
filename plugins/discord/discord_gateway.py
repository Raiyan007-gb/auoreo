"""Discord Gateway v10 session for plugins/discord (kept apart for the 500-line cap).

Hello (op 10) → Identify (op 2, intents) or Resume (op 6, session_id + last seq on
`resume_gateway_url`) → heartbeats (op 1) every `heartbeat_interval` with a jittered first beat;
a missing ACK (op 11) closes and resumes. op 7 → resume; op 9 → re-identify unless `d` is true;
the non-resumable close codes in `FATAL_CLOSE` stop the loop with an honest reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
from typing import Any

INTENTS = (
    (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)
)  # GUILDS, GUILD_MESSAGES, DIRECT_MESSAGES, MESSAGE_CONTENT
FATAL_CLOSE = {
    4004: "authentication failed (bad bot token)",
    4010: "invalid shard",
    4011: "sharding required",
    4012: "invalid API version",
    4013: "invalid intents",
    4014: "disallowed intents: enable MESSAGE CONTENT INTENT on the Bot page of the Developer Portal",
}


class DiscordError(RuntimeError):
    pass


class FatalGateway(DiscordError):
    pass


class Gateway:
    """One Gateway v10 session with heartbeat, resume and re-identify."""

    def __init__(self, plugin: Any, connect: Any = None) -> None:
        self.p = plugin
        self.connect = connect
        self.session_id: str | None = None
        self.seq: int | None = None
        self.resume_url: str | None = None
        self.acked = True
        self.identifies = 0
        self.resumes = 0

    def _connector(self) -> Any:
        if self.connect is None:
            from websockets.asyncio.client import connect

            self.connect = connect
        return self.connect

    async def run(self, url: str) -> None:
        backoff = 1.0
        while True:
            target = self.resume_url if self.session_id and self.resume_url else url
            try:
                await self.session(target + ("&" if "?" in target else "?") + "v=10&encoding=json")
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except FatalGateway as exc:
                self.p.fatal = str(exc)
                self.p.host.log("error", f"discord gateway stopped: {exc}")
                with contextlib.suppress(Exception):
                    await self.p.host.audit("gateway_fatal", {"reason": str(exc)[:200]})
                return
            except Exception as exc:
                self.p.host.log("warning", f"discord gateway dropped: {type(exc).__name__}: {exc}")
                await asyncio.sleep(backoff * self.p.backoff_scale)
                backoff = min(backoff * 2, 60.0)

    async def _send(self, ws: Any, op: int, d: Any) -> None:
        await ws.send(json.dumps({"op": op, "d": d}))

    async def _beat(self, ws: Any, interval: float) -> None:
        await asyncio.sleep(interval * random.random())
        while True:
            if not self.acked:
                await ws.close(4000, "heartbeat not acknowledged")
                return
            self.acked = False
            await self._send(ws, 1, self.seq)
            await asyncio.sleep(interval)

    async def session(self, url: str) -> None:
        from websockets.exceptions import ConnectionClosed

        beat: asyncio.Task[None] | None = None
        try:
            async with self._connector()(url, max_size=8 * 2**20) as ws:
                hello = json.loads(await ws.recv())
                if hello.get("op") != 10:
                    raise DiscordError(f"expected Hello, got op {hello.get('op')}")
                self.acked = True
                beat = asyncio.create_task(self._beat(ws, hello["d"]["heartbeat_interval"] / 1000.0))
                token = await self.p.secret("discord/bot_token")
                if self.session_id and self.seq is not None:
                    self.resumes += 1
                    await self._send(ws, 6, {"token": token, "session_id": self.session_id, "seq": self.seq})
                else:
                    self.identifies += 1
                    props = {"os": "linux", "browser": "auoreo", "device": "auoreo"}
                    await self._send(ws, 2, {"token": token, "intents": INTENTS, "properties": props})
                async for raw in ws:
                    if await self.handle(ws, json.loads(raw)):
                        return
        except ConnectionClosed as exc:
            code = getattr(exc.rcvd, "code", None)
            if code in FATAL_CLOSE:
                raise FatalGateway(f"close {code}: {FATAL_CLOSE[code]}") from exc
            raise DiscordError(f"connection closed ({code})") from exc
        finally:
            if beat is not None:
                beat.cancel()

    async def handle(self, ws: Any, msg: dict[str, Any]) -> bool:
        """One gateway payload (dict dispatch on the opcode); True = reconnect now."""
        ops = {0: self._op_dispatch, 1: self._op_heartbeat, 7: self._op_reconnect, 9: self._op_invalid}
        if msg.get("op") == 11:
            self.acked = True
            return False
        fn = ops.get(int(msg.get("op", -1)))
        return bool(await fn(ws, msg)) if fn is not None else False

    async def _op_dispatch(self, ws: Any, msg: dict[str, Any]) -> bool:
        self.seq = msg.get("s", self.seq)
        await self.dispatch(str(msg.get("t")), msg.get("d") or {})
        return False

    async def _op_heartbeat(self, ws: Any, msg: dict[str, Any]) -> bool:
        await self._send(ws, 1, self.seq)
        return False

    async def _op_reconnect(self, ws: Any, msg: dict[str, Any]) -> bool:
        await ws.close(4000, "reconnect requested")
        return True

    async def _op_invalid(self, ws: Any, msg: dict[str, Any]) -> bool:
        if not msg.get("d"):  # not resumable → identify from scratch
            self.session_id, self.seq, self.resume_url = None, None, None
        await asyncio.sleep(random.uniform(1, 5) * self.p.backoff_scale)
        await ws.close(4000, "invalid session")
        return True

    async def dispatch(self, kind: str, d: dict[str, Any]) -> None:
        if kind == "READY":
            self.session_id, self.resume_url = d.get("session_id"), d.get("resume_gateway_url")
        if kind == "THREAD_CREATE" and d.get("parent_id"):
            self.p.thread_parent[str(d["id"])] = str(d["parent_id"])
        handlers = {"READY": self.p.on_ready, "MESSAGE_CREATE": self.p.on_message}
        handlers["INTERACTION_CREATE"] = self.p.on_interaction
        if (fn := handlers.get(kind)) is None:
            return
        try:
            await fn(d)
        except Exception as exc:  # one bad event (REST hiccup, odd payload) must not drop the session
            self.p.host.log("warning", f"discord: {kind} handler failed: {type(exc).__name__}: {exc}")
