"""STT through the voice media sidecar (`auoreo.voice.sidecar`)."""

from __future__ import annotations

from collections.abc import Sequence

from auoreo.backends import BackendStatus
from auoreo.backends.stt import Segment, SttBackend, Transcript
from auoreo.voice.sidecar import encode, get_host


class Backend(SttBackend):
    async def check(self) -> BackendStatus:
        return await get_host(self.settings).check()

    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        params = {"wav_b64": encode(wav), "language": language, "keyterms": list(keyterms)}
        r = await get_host(self.settings).call("transcribe", params)
        segs = [Segment(float(s["start"]), float(s["end"]), str(s["text"])) for s in r.get("segments") or []]
        return Transcript(str(r.get("text", "")), segs, r.get("language"), r.get("confidence"))

    async def close(self) -> None:
        await get_host(self.settings).close()
