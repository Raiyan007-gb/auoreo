"""edge-tts as a subprocess (licence isolation, spec §13: never imported in the server).

`<cmd> --text … --voice … --rate=+N% --write-media <tmp.mp3>`; the child gets the scrubbed
`child_env` (no provider keys). One process per chunk; 60 s timeout; stderr tail on failure.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

from auoreo.backends import BackendStatus
from auoreo.backends.tts import Audio, TtsBackend, clamp_speed
from auoreo.core.secrets import child_env
from auoreo.voice.audio import AudioError, VoiceUnavailable
from auoreo.voice.cloud_stt import voice_g6

INSTALL = "uv tool install edge-tts (or set g6.voice.edge_tts_command to a venv's python -m edge_tts)"
TIMEOUT_S = 60.0


def available(exe: str) -> bool:
    return shutil.which(exe) is not None or Path(exe).is_file()


def rate_arg(speed: float) -> str:
    pct = round((clamp_speed(speed) - 1.0) * 100)
    return f"--rate={pct:+d}%"


class Backend(TtsBackend):
    chunk_limit = 5000

    @property
    def command(self) -> list[str]:
        return list(voice_g6(self.settings).edge_tts_command) or ["edge-tts"]

    async def check(self) -> BackendStatus:
        exe = self.command[0]
        if not available(exe):
            return BackendStatus(False, f"{exe} not found (edge-tts runs out of process)", remedy=INSTALL)
        return BackendStatus(True, f"edge-tts via {exe} (out of process; unofficial online service)")

    async def synthesize(self, text: str, *, voice: str | None = None, speed: float = 1.0) -> Audio:
        exe = self.command[0]
        if not available(exe):
            raise VoiceUnavailable(f"tts/edge_tts: {exe} not found; {INSTALL}")
        chosen = voice or voice_g6(self.settings).edge_tts_voice
        with tempfile.TemporaryDirectory(prefix="auoreo-edge-") as tmp:
            out = Path(tmp) / "out.mp3"
            argv = [
                *self.command,
                "--text",
                text,
                "--voice",
                chosen,
                rate_arg(speed),
                "--write-media",
                str(out),
            ]
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=child_env([], None),
            )
            try:
                _, err = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                raise VoiceUnavailable(f"edge-tts timed out after {TIMEOUT_S:.0f} s") from None
            if proc.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
                tail = err.decode(errors="replace").strip().splitlines()[-3:]
                raise AudioError(f"edge-tts failed (exit {proc.returncode}): {' | '.join(tail)[:300]}")
            return Audio(out.read_bytes(), "audio/mpeg")
