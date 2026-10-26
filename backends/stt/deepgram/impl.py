"""Deepgram live speech-to-text over a raw WebSocket (no SDK; `websockets` imported lazily).

`wss://…/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000&channels=1&interim_results=true`
with `Authorization: Token <key>`. Audio goes out as binary frames; `Results` messages carry
`channel.alternatives[0].transcript` and `is_final`. After the audio, `{"type": "CloseStream"}`
makes the server flush the remaining results, send `Metadata` and close.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any
from urllib.parse import urlencode

from auoreo.backends import BackendStatus
from auoreo.backends.stt import Segment, SttBackend, Transcript, TranscriptDelta, check_upload
from auoreo.voice.audio import AudioError, VoiceUnavailable, duration_s, read_wav
from auoreo.voice.cloud import key_status, require_key
from auoreo.voice.cloud_stt import voice_g6

KEY = "DEEPGRAM_API_KEY"
INSTALL = "uv pip install 'auoreo[voice-stream]'"
FRAME_BYTES = 8000  # 250 ms of 16 kHz int16 mono
TIMEOUT_S = 30.0


class Backend(SttBackend):
    streaming = True

    def url(self, language: str | None, keyterms: Sequence[str]) -> str:
        cfg = voice_g6(self.settings)
        params: list[tuple[str, str]] = [
            ("model", cfg.deepgram_model),
            ("encoding", "linear16"),
            ("sample_rate", "16000"),
            ("channels", "1"),
            ("interim_results", "true"),
            ("punctuate", "true"),
            ("smart_format", "true"),
        ]
        if language:
            params.append(("language", language))
        params += [("keyterm", t) for t in [k.strip() for k in keyterms if k.strip()][:50]]
        return f"{cfg.deepgram_url}?{urlencode(params)}"

    async def check(self) -> BackendStatus:
        if importlib.util.find_spec("websockets") is None:
            return BackendStatus(False, "the websockets package is not installed", remedy=INSTALL)
        return key_status(
            self.settings, KEY, f"Deepgram {voice_g6(self.settings).deepgram_model} (streaming)"
        )

    async def transcribe(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> Transcript:
        text, lang = "", language
        async for d in self.stream(wav, language=language, keyterms=keyterms):
            if d.done:
                text, lang = d.text, d.language or language
        segs = [Segment(0.0, duration_s(wav), text)] if text else []
        return Transcript(text, segs, lang, None)

    async def stream(
        self, wav: bytes, *, language: str | None = None, keyterms: Sequence[str] = ()
    ) -> AsyncIterator[TranscriptDelta]:
        check_upload(wav)
        key = require_key(self.settings, KEY)
        try:
            from websockets.asyncio.client import connect
            from websockets.exceptions import WebSocketException
        except ImportError:
            raise VoiceUnavailable(f"stt/deepgram needs the websockets package; {INSTALL}") from None
        pcm = read_wav(wav).pcm
        finals: list[str] = []
        try:
            async with asyncio.timeout(TIMEOUT_S):
                async with connect(
                    self.url(language, keyterms), additional_headers={"Authorization": f"Token {key}"}
                ) as ws:
                    sender = asyncio.create_task(_send(ws, pcm))
                    try:
                        async for raw in ws:
                            delta = _parse(raw, finals)
                            if delta is not None:
                                yield delta
                    finally:
                        sender.cancel()
        except TimeoutError:
            raise VoiceUnavailable(f"deepgram stream timed out after {TIMEOUT_S:.0f} s") from None
        except WebSocketException as exc:
            raise VoiceUnavailable(f"deepgram stream failed: {type(exc).__name__}") from None
        except OSError as exc:
            raise VoiceUnavailable(f"deepgram connection failed: {type(exc).__name__}") from None
        yield TranscriptDelta(" ".join(finals).strip(), final=True, done=True, language=language)


async def _send(ws: Any, pcm: bytes) -> None:
    for i in range(0, len(pcm), FRAME_BYTES):
        await ws.send(pcm[i : i + FRAME_BYTES])
    await ws.send(json.dumps({"type": "CloseStream"}))


def _parse(raw: Any, finals: list[str]) -> TranscriptDelta | None:
    if isinstance(raw, bytes):
        return None
    try:
        msg = json.loads(raw)
    except ValueError:
        return None
    kind = msg.get("type")
    if kind == "Error" or "err_code" in msg:
        raise AudioError(f"deepgram error: {str(msg.get('description') or msg.get('err_msg'))[:200]}")
    if kind != "Results":
        return None
    alts = (msg.get("channel") or {}).get("alternatives") or [{}]
    text = str(alts[0].get("transcript", "")).strip()
    if not text:
        return None
    final = bool(msg.get("is_final"))
    if final:
        finals.append(text)
    return TranscriptDelta(text, final=final)
