"""BLE probe stub: honest `check()`, `start()` refuses (no scanner implemented)."""

from __future__ import annotations

from auoreo.backends import BackendStatus
from auoreo.backends.sensor import Sample, SensorProbe
from auoreo.tools.context import ToolError

DETAIL = "BLE probe is a stub (no scanner implemented)"
REMEDY = "bridge BLE sensors through an MQTT gateway and use the mqtt plugin"


class Backend(SensorProbe):
    name = "ble"

    async def check(self) -> BackendStatus:
        return BackendStatus(False, DETAIL, remedy=REMEDY)

    def capabilities(self) -> dict[str, bool]:
        return {"video": False, "audio": False, "telemetry": False}

    async def start(self, source: str) -> None:
        raise ToolError(f"{DETAIL}; {REMEDY}")

    async def sample(self) -> Sample:
        raise ToolError(DETAIL)

    async def stop(self) -> None:
        return None
