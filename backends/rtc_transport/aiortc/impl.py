"""aiortc RTC transport: a thin wrapper over the in-process `RtcManager` (`auoreo.rtc.peers`).

`join` = offer/answer through the shared manager (the same peers `POST /api/rtc/offer`
creates), `leave` = close, `describe` = the manager's listing. Backends are constructed with
`Settings` only, so the API binds the live `Services` with `bind()` before the first join.
"""

from __future__ import annotations

from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.rtc_transport import JoinInfo, RtcTransport
from auoreo.rtc.peers import INSTALL_HINT, aiortc_available, manager_for


class Backend(RtcTransport):
    name = "aiortc"

    def __init__(self, settings: Any) -> None:
        super().__init__(settings)
        self._svc: Any = None

    def bind(self, services: Any) -> None:
        self._svc = services

    def _manager(self) -> Any:
        if self._svc is None:
            raise RuntimeError("aiortc transport is not bound to the server (call bind(services))")
        return manager_for(self._svc)

    async def check(self) -> BackendStatus:
        if not aiortc_available():
            return BackendStatus(False, "aiortc is not installed", remedy=INSTALL_HINT)
        ice = list(getattr(self.settings, "rtc_ice_servers", []) or [])
        turn = sum(1 for u in ice if u.startswith(("turn:", "turns:")))
        return BackendStatus(True, f"in-process P2P; {len(ice)} ICE server(s), {turn} TURN")

    async def join(self, *, session_id: str | None, identity: str, offer_sdp: str | None) -> JoinInfo:
        if not offer_sdp:
            raise ValueError("the aiortc transport needs an SDP offer (field 'sdp')")
        rtc_id, answer = await self._manager().offer(offer_sdp, "offer", session_id)
        return JoinInfo("aiortc", session_id, sdp=answer, extra={"join_id": rtc_id, "rtc_id": rtc_id})

    async def leave(self, join_id: str) -> None:
        await self._manager().close(join_id)

    async def describe(self) -> list[dict[str, Any]]:
        if self._svc is None or self._svc.rtc is None:
            return []
        return list(self._manager().describe())
