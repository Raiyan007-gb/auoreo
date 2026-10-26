"""Barge-in noise-floor calibration (spec §13.5: rolling-floor VAD, calibrate 400 ms /
sustain 300 ms).

Each RTC peer measures its own background level: the first `calibrate_ms` of inbound audio
heard while the agent is silent and the VAD reports no speech sets the floor (mean RMS);
afterwards quiet frames keep adapting it slowly (EMA). While the agent speaks, a frame only
counts as "loud" for barge-in when its RMS is above both the configured speech threshold
and `floor × floor_multiplier`, so a noisy room (fan, traffic) does not cut the agent off.
The sustain window (≥ 80 % loud over `sustain_ms`) lives in `rtc_bridge`.
Until calibration completes the floor is 0 (the plain threshold applies).
"""

from __future__ import annotations

from dataclasses import dataclass

EMA_ALPHA = 0.02


@dataclass
class NoiseFloor:
    calibrate_ms: float = 400.0
    multiplier: float = 3.0
    floor: float = 0.0
    calibrated_ms: float = 0.0
    _acc: float = 0.0

    @property
    def ready(self) -> bool:
        return self.calibrated_ms >= self.calibrate_ms

    def feed(self, frame_ms: float, level: float, quiet: bool) -> None:
        """Feed one inbound frame heard while the agent is silent; `quiet` = VAD says no speech."""
        if not quiet:
            return
        if not self.ready:
            self._acc += level * frame_ms
            self.calibrated_ms += frame_ms
            if self.ready:
                self.floor = self._acc / max(self.calibrated_ms, 1e-9)
            return
        self.floor += EMA_ALPHA * (level - self.floor)

    def threshold(self, base: float) -> float:
        """The loudness a barge-in frame must exceed."""
        return max(base, self.floor * self.multiplier) if self.ready else base
