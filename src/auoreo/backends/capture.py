"""Screen capture backend kind (`backends/capture/<name>/`)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.core.settings import Settings

Region = tuple[int, int, int, int]
"""(left, top, width, height) in virtual-screen pixels."""


class CaptureBackend(ABC):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus:
        """Whether a display can be captured here (never raises)."""

    @abstractmethod
    async def monitors(self) -> list[dict[str, Any]]:
        """Monitors as `{index, left, top, width, height}`; index 0 is the whole virtual
        screen and 1… are physical monitors (mss convention)."""

    @abstractmethod
    async def grab(self, monitor: int | None = None, region: Region | None = None) -> bytes:
        """PNG bytes of one monitor (default: primary, 1) or of `region`."""
