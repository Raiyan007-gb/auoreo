"""OpenAI Realtime WebRTC transport (spec §13/§22-6, §26: ephemeral mint server-side).

1. `POST {base}/realtime/client_secrets` with the standard key and
   `{"session": {"type": "realtime", "model", "audio": {"output": {"voice"}}}}` plus header
   `OpenAI-Safety-Identifier: sha256(session)[:32]` → `{"value": "ek_…", "expires_at": …}`
   (older answers nest it as `client_secret.value`).
2. With an SDP offer: `POST {base}/realtime/calls` (`Content-Type: application/sdp`, bearer =
   the ephemeral secret) → the SDP answer; the call id comes from the `Location` header.
   Without an offer the client gets the ephemeral secret and `calls` URL and exchanges itself.
3. `leave` hangs the call up (`POST {base}/realtime/calls/{id}/hangup`, best effort).

Only `{session_id, minted_at, model, outcome}` is kept/audited; secrets and SDP never are.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from typing import Any

import httpx

from auoreo.backends import BackendStatus
from auoreo.backends.rtc_transport import JoinInfo, RtcTransport
from auoreo.core.settings_g6 import RealtimeSettings
from auoreo.voice.cloud import key_status, secret

KEY = "OPENAI_API_KEY"
TIMEOUT_S = 20.0


class RealtimeError(ConnectionError):
    """The OpenAI Realtime API refused or failed (HTTP 502 at `/api/rtc/join`)."""


class Backend(RtcTransport):
    name = "openai_realtime"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self.calls: dict[str, dict[str, Any]] = {}
        self.mints: list[dict[str, Any]] = []
        """Audit trail of mints (no secret values)."""
        self._svc: Any = None

    def bind(self, services: Any) -> None:
        self._svc = services

    @property
    def cfg(self) -> RealtimeSettings:
        g6 = getattr(self.settings, "g6", None)
        return g6.voice.realtime if g6 is not None else RealtimeSettings()

    @property
    def base(self) -> str:
        return self.cfg.base_url.rstrip("/")

    def _key(self) -> str:
        key = secret(self.settings, KEY)
        if not key:
            raise RuntimeError(f"{KEY} is not set; run `auoreo secrets set {KEY}`")
        return key

    async def check(self) -> BackendStatus:
        return key_status(self.settings, KEY, f"OpenAI Realtime {self.cfg.model} (WebRTC, ephemeral mint)")

    def session_config(self) -> dict[str, Any]:
        cfg = self.cfg
        audio: dict[str, Any] = {"output": {"voice": cfg.voice}}
        if cfg.transcription_model:
            audio["input"] = {"transcription": {"model": cfg.transcription_model}}
        return {"type": "realtime", "model": cfg.model, "audio": audio}

    async def mint(self, session_id: str | None) -> dict[str, Any]:
        """Ephemeral client secret `{value, expires_at}` for one session (never persisted)."""
        safety = hashlib.sha256((session_id or "anonymous").encode()).hexdigest()[:32]
        headers = {"Authorization": f"Bearer {self._key()}", "OpenAI-Safety-Identifier": safety}
        record: dict[str, Any] = {"session_id": session_id, "minted_at": time.time(), "model": self.cfg.model}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                r = await client.post(
                    f"{self.base}/realtime/client_secrets",
                    headers=headers,
                    json={"session": self.session_config()},
                )
        except httpx.HTTPError as exc:
            await self._audit({**record, "outcome": f"error:{type(exc).__name__}"})
            raise RealtimeError(f"realtime mint failed: {type(exc).__name__}") from None
        if r.status_code >= 400:
            await self._audit({**record, "outcome": f"http_{r.status_code}"})
            raise RealtimeError(f"realtime mint failed: HTTP {r.status_code}")
        body = r.json()
        nested = body.get("client_secret") if isinstance(body.get("client_secret"), dict) else {}
        value = body.get("value") or nested.get("value")
        if not value:
            await self._audit({**record, "outcome": "no_secret"})
            raise RealtimeError("realtime mint returned no client secret")
        await self._audit({**record, "outcome": "ok"})
        return {"value": str(value), "expires_at": body.get("expires_at") or nested.get("expires_at")}

    async def _audit(self, record: dict[str, Any]) -> None:
        self.mints.append(record)
        if self._svc is not None:
            await self._svc.emit(record.get("session_id") or "server", "rtc.realtime_minted", dict(record))

    async def exchange(self, ephemeral: str, offer_sdp: str) -> tuple[str, str | None]:
        """SDP offer → (answer, call id). The SDP itself is never logged."""
        headers = {"Authorization": f"Bearer {ephemeral}", "Content-Type": "application/sdp"}
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                r = await client.post(
                    f"{self.base}/realtime/calls", headers=headers, content=offer_sdp.encode()
                )
        except httpx.HTTPError as exc:
            raise RealtimeError(f"realtime SDP exchange failed: {type(exc).__name__}") from None
        if r.status_code >= 400:
            raise RealtimeError(f"realtime SDP exchange failed: HTTP {r.status_code}")
        answer = r.text
        if not answer.startswith("v=0"):
            raise RealtimeError("realtime SDP exchange returned no SDP answer")
        location = r.headers.get("location") or ""
        call_id = location.rstrip("/").rsplit("/", 1)[-1] or None
        return answer, call_id

    async def join(self, *, session_id: str | None, identity: str, offer_sdp: str | None) -> JoinInfo:
        secret_ = await self.mint(session_id)
        join_id = f"rt_{uuid.uuid4().hex[:12]}"
        calls_url = f"{self.base}/realtime/calls"
        extra: dict[str, Any] = {
            "join_id": join_id,
            "model": self.cfg.model,
            "expires_at": secret_["expires_at"],
        }
        if offer_sdp is None:
            self.calls[join_id] = {"join_id": join_id, "session_id": session_id, "call_id": None}
            return JoinInfo("openai_realtime", session_id, url=calls_url, token=secret_["value"], extra=extra)
        answer, call_id = await self.exchange(secret_["value"], offer_sdp)
        self.calls[join_id] = {"join_id": join_id, "session_id": session_id, "call_id": call_id}
        return JoinInfo("openai_realtime", session_id, sdp=answer, extra={**extra, "call_id": call_id})

    async def leave(self, join_id: str) -> None:
        call = self.calls.pop(join_id, None)
        if call is None or not call.get("call_id"):
            return
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
                await client.post(
                    f"{self.base}/realtime/calls/{call['call_id']}/hangup",
                    headers={"Authorization": f"Bearer {self._key()}"},
                )
        except (httpx.HTTPError, RuntimeError):
            return  # best effort: the call also ends when the peer closes

    async def describe(self) -> list[dict[str, Any]]:
        return [dict(c) for c in self.calls.values()]
