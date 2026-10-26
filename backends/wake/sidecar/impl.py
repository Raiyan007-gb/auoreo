"""Wake word through the voice media sidecar (`auoreo.voice.host` `wake` method, I8).

The sidecar loads `i8.wake.host_backend` (e.g. sherpa_onnx, or the NC openwakeword weights
that must never load in the server process)."""

from __future__ import annotations

from auoreo.backends import BackendStatus
from auoreo.backends.wake import WakeBackend, WakeHit
from auoreo.voice.sidecar import encode, get_host


class Backend(WakeBackend):
    async def check(self) -> BackendStatus:
        return await get_host(self.settings).check()

    async def detect(self, wav: bytes) -> WakeHit | None:
        r = await get_host(self.settings).call("wake", {"wav_b64": encode(wav)})
        hit = r.get("hit") if isinstance(r, dict) else None
        if not isinstance(hit, dict):
            return None
        return WakeHit(str(hit.get("phrase", "")), float(hit.get("start", 0.0)), float(hit.get("score", 1.0)))

    async def close(self) -> None:
        await get_host(self.settings).close()
