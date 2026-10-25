"""Sensor probe backend kind (`backends/sensor/<name>/`, M6; spec §14.1 `SensorProbe`).

Probes import their libraries lazily inside `start()`, are enabled per session, and emit
`presence + reading + confidence` samples. Attachments: USB/UVC index, RTSP URL (read-only;
no PTZ without approval), MQTT topic glob (via the mqtt plugin), BLE (stub).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from auoreo.backends import BackendStatus


@dataclass
class Sample:
    presence: bool
    reading: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    image: bytes | None = None
    """A JPEG/PNG frame for camera probes (fed to detection/vision tools)."""


class SensorProbe(ABC):
    name: str = "sensor"

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus: ...

    @abstractmethod
    def capabilities(self) -> dict[str, bool]:
        """e.g. `{"video": True, "audio": False, "telemetry": False}`."""

    @abstractmethod
    async def start(self, source: str) -> None: ...

    @abstractmethod
    async def sample(self) -> Sample: ...

    @abstractmethod
    async def stop(self) -> None: ...
