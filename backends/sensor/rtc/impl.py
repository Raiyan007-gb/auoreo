"""RTC video probe: reads the in-memory frame store filled by `perception.rtc.sample_video`."""

from __future__ import annotations

from auoreo.backends import BackendStatus
from auoreo.backends.sensor import Sample, SensorProbe
from auoreo.perception import rtc
from auoreo.tools.context import ToolError


class Backend(SensorProbe):
    name = "rtc"

    def __init__(self, settings: object) -> None:
        super().__init__(settings)
        self.session_id: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.settings, "perception_sample_rtc_video", False))

    async def check(self) -> BackendStatus:
        if not self.enabled:
            return BackendStatus(
                False,
                "RTC video sampling is off",
                remedy='set "perception_sample_rtc_video": true in settings.json',
            )
        every = getattr(self.settings, "perception_rtc_interval_s", 5.0)
        return BackendStatus(True, f"RTC video frames every {every}s (memory only)")

    def capabilities(self) -> dict[str, bool]:
        return {"video": True, "audio": False, "telemetry": False}

    async def start(self, source: str) -> None:
        if not source:
            raise ToolError("rtc source must be a session id")
        self.session_id = source

    async def sample(self) -> Sample:
        if self.session_id is None:
            raise ToolError("rtc probe is not started")
        item = rtc.latest(self.session_id)
        if item is None:
            return Sample(False, {"frame": None, "reason": "no recent RTC video frame"}, 0.0, None)
        age, jpeg = item
        return Sample(True, {"frame_age_s": round(age, 1), "bytes": len(jpeg)}, 1.0, jpeg)

    async def stop(self) -> None:
        self.session_id = None
