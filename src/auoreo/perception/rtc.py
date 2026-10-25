"""Optional RTC video sampling (`perception_sample_rtc_video`, default off).

When enabled, `peers._on_track` runs `sample_video` for a session's video track instead of
just draining it: one frame every `perception_rtc_interval_s` seconds is JPEG-encoded into
an in-memory store keyed by session id (never written to disk). The `rtc` sensor probe
(`backends/sensor/rtc`) reads the latest frame from here.
"""

from __future__ import annotations

import io
import threading
import time
from typing import Any

MAX_SESSIONS = 16
STALE_S = 120.0
_lock = threading.Lock()
_frames: dict[str, tuple[float, bytes]] = {}


def put(session_id: str, jpeg: bytes, now: float | None = None) -> None:
    with _lock:
        _frames[session_id] = (now if now is not None else time.monotonic(), jpeg)
        while len(_frames) > MAX_SESSIONS:  # drop the oldest
            oldest = min(_frames, key=lambda k: _frames[k][0])
            _frames.pop(oldest, None)


def latest(session_id: str, max_age_s: float = STALE_S) -> tuple[float, bytes] | None:
    """`(age_s, jpeg)` of the newest frame for `session_id`, or None when absent/stale."""
    with _lock:
        item = _frames.get(session_id)
    if item is None:
        return None
    age = time.monotonic() - item[0]
    return None if age > max_age_s else (age, item[1])


def forget(session_id: str) -> None:
    with _lock:
        _frames.pop(session_id, None)


def _jpeg(frame: Any) -> bytes:
    img = frame.to_image()  # av.VideoFrame → PIL (aiortc ships PyAV)
    img.thumbnail((1280, 1280))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()


async def sample_video(track: Any, session_id: str, interval_s: float) -> None:
    """Consume `track` (frames must be read or aiortc buffers them); keep one every interval."""
    last: float | None = None  # first frame always kept (monotonic may be < interval)
    try:
        while True:
            frame = await track.recv()
            now = time.monotonic()
            if last is not None and now - last < interval_s:
                continue
            last = now
            try:
                put(session_id, _jpeg(frame), now)
            except Exception:  # a bad frame must not end the track
                continue
    except Exception:  # MediaStreamError at end of track
        return
