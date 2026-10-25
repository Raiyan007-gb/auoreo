"""RTC transport backend kind (`backends/rtc_transport/<name>/`, M6).

`aiortc` (default, in-process P2P, `src/auoreo/rtc/`) or `livekit` (SFU: rooms, SIP
telephony, egress). Signaling stays on the one AUOREO port; approvals never travel over a
media data channel; SDP is never logged (spec §19.12, §26.8).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from auoreo.backends import BackendStatus


@dataclass(frozen=True)
class JoinInfo:
    """What a browser/phone client needs to join: an SDP answer (P2P) or a room token (SFU)."""

    transport: str
    session_id: str | None
    sdp: str | None = None
    url: str | None = None
    token: str | None = None
    room: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class RtcTransport(ABC):
    name: str = "rtc_transport"

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus: ...

    @abstractmethod
    async def join(self, *, session_id: str | None, identity: str, offer_sdp: str | None) -> JoinInfo:
        """Create/join the media session for `session_id`."""

    @abstractmethod
    async def leave(self, join_id: str) -> None: ...

    async def describe(self) -> list[dict[str, Any]]:
        """Active media sessions (no SDP, no tokens)."""
        return []
