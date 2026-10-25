"""Fake sensor probe: scripted presence + readings and a generated frame (Pillow only).

`source` is a free label, or `script:1,0,1` to script presence per sample (cycled). A frame
with presence shows a tall "person" block (which the fake detector labels `person`) next to
a small square object; without presence only the object is drawn.
"""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from auoreo.backends import BackendStatus
from auoreo.backends.sensor import Sample, SensorProbe
from auoreo.tools.context import ToolError

DEFAULT_SCRIPT = (True, True, False)
SIZE = (160, 120)


def _frame(presence: bool, n: int) -> bytes:
    img = Image.new("RGB", SIZE, (24, 24, 32))
    draw = ImageDraw.Draw(img)
    draw.rectangle((110, 70, 140, 100), fill=(40, 160, 255))  # object
    if presence:
        draw.rectangle((30 + 4 * (n % 5), 20, 60 + 4 * (n % 5), 100), fill=(240, 80, 60))  # person
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class Backend(SensorProbe):
    name = "fake"
    is_test_backend = True

    def __init__(self, settings: object) -> None:
        super().__init__(settings)
        self.script: tuple[bool, ...] = DEFAULT_SCRIPT
        self.source: str | None = None
        self.count = 0
        self.stopped = False

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "fake sensor probe (test backend; scripted presence + generated frame)")

    def capabilities(self) -> dict[str, bool]:
        return {"video": True, "audio": False, "telemetry": True}

    async def start(self, source: str) -> None:
        if source.startswith("script:"):
            try:
                self.script = tuple(bool(int(v)) for v in source[7:].split(",") if v.strip())
            except ValueError:
                raise ToolError("fake script must look like script:1,0,1") from None
            if not self.script:
                raise ToolError("fake script is empty")
        self.source, self.count, self.stopped = source, 0, False

    async def sample(self) -> Sample:
        if self.source is None or self.stopped:
            raise ToolError("fake probe is not started")
        n = self.count
        self.count += 1
        presence = self.script[n % len(self.script)]
        reading = {"index": n, "temperature_c": round(21.0 + 0.5 * (n % 4), 1), "motion": presence}
        return Sample(presence, reading, 0.9 if presence else 0.8, _frame(presence, n))

    async def stop(self) -> None:
        self.stopped = True
