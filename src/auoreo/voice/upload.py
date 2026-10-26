"""Shape audio before a cloud STT upload (spec §13.1, §25.1 pre-send matrix).

1. **Silence trim** (`i8.stt.trim_silence`, default on): leading/trailing 20 ms frames whose
   RMS stays below `TRIM_RMS` are cut, keeping `PAD_MS` of context on each side. All-silent
   audio is left alone (the VAD gate upstream decides about it).
2. **AAC** (`i8.stt.cloud_aac`, opt-in): 16 kHz mono AAC in fragmented MP4 (`audio.m4a`,
   accepted by OpenAI/Groq/Mistral). Without ffmpeg, or when encoding fails, the WAV is sent.

`prepare_upload` returns `(filename, bytes, media_type)` for the multipart `file` field.
"""

from __future__ import annotations

import logging
from typing import Any

from auoreo.core.settings_i8 import i8_of
from auoreo.voice.audio import (
    SAMPLE_RATE,
    AudioError,
    VoiceUnavailable,
    read_wav,
    rms,
    run_ffmpeg,
    wav_from_pcm,
)

log = logging.getLogger(__name__)

FRAME_MS = 20
TRIM_RMS = 0.006
PAD_MS = 200
AAC_ARGS = (
    "-i",
    "pipe:0",
    "-ar",
    str(SAMPLE_RATE),
    "-ac",
    "1",
    "-c:a",
    "aac",
    "-b:a",
    "48k",
    "-movflags",
    "+frag_keyframe+empty_moov",
    "-f",
    "mp4",
    "pipe:1",
)


def trim_silence(wav: bytes, threshold: float = TRIM_RMS, pad_ms: int = PAD_MS) -> bytes:
    """Cut leading/trailing silence (keeps `pad_ms` around speech; all-silence unchanged)."""
    info = read_wav(wav)
    if info.sample_width != 2 or info.channels != 1:
        return wav
    step = max(2, int(info.sample_rate * FRAME_MS / 1000) * 2)
    frames = [info.pcm[i : i + step] for i in range(0, len(info.pcm), step)]
    loud = [i for i, f in enumerate(frames) if rms(f) > threshold]
    if not loud:
        return wav
    pad = pad_ms // FRAME_MS
    first, last = max(0, loud[0] - pad), min(len(frames), loud[-1] + 1 + pad)
    if first == 0 and last == len(frames):
        return wav
    return wav_from_pcm(b"".join(frames[first:last]), info.sample_rate, 1, info.comment)


async def prepare_upload(wav: bytes, settings: Any | None) -> tuple[str, bytes, str]:
    if settings is None:
        return "audio.wav", wav, "audio/wav"
    cfg = i8_of(settings).stt
    data = wav
    if cfg.trim_silence:
        try:
            data = trim_silence(wav)
        except AudioError:
            data = wav
    if cfg.cloud_aac:
        try:
            return "audio.m4a", await run_ffmpeg(data, AAC_ARGS), "audio/mp4"
        except (AudioError, VoiceUnavailable) as exc:
            log.info("cloud upload: AAC encoding unavailable (%s); sending WAV", exc)
    return "audio.wav", data, "audio/wav"
