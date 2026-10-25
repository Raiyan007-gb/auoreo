"""Audio helpers for the voice plane (no audio libraries: stdlib `wave` + the `ffmpeg` binary).

`normalize` turns any upload (webm/ogg/mp3/m4a/wav/…) into the one format every backend
consumes: WAV, 16 kHz, mono, int16 — via
`ffmpeg -i pipe:0 -ar 16000 -ac 1 -c:a pcm_s16le -f wav pipe:1` (25 MB input cap, 30 s
timeout). ffmpeg cannot seek a pipe, so its RIFF/data sizes read `0xFFFFFFFF`; `read_wav`
tolerates that and `normalize` re-emits a clean header. A WAV `ICMT` comment (LIST/INFO)
survives normalization; the fake STT backend uses it as the scripted transcript.
"""

from __future__ import annotations

import asyncio
import math
import shutil
import struct
from array import array
from dataclasses import dataclass

from auoreo.tools.context import ToolError

SAMPLE_RATE = 16_000
MAX_INPUT_BYTES = 25 * 1024 * 1024
FFMPEG_TIMEOUT_S = 30.0
FFMPEG_ARGS = ("-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", "pipe:1")
_WAV_TYPES = {"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"}
# Output formats `transcode` can produce: format → (ffmpeg args, media type).
FORMATS: dict[str, tuple[tuple[str, ...], str]] = {
    "wav": (("-c:a", "pcm_s16le", "-f", "wav"), "audio/wav"),
    "mp3": (("-c:a", "libmp3lame", "-b:a", "64k", "-f", "mp3"), "audio/mpeg"),
    "opus": (("-c:a", "libopus", "-b:a", "48k", "-f", "ogg"), "audio/ogg"),
}
MEDIA_EXT = {"audio/wav": "wav", "audio/mpeg": "mp3", "audio/ogg": "ogg"}


class AudioError(ToolError):
    """Undecodable input or ffmpeg failure (API: 422)."""


class AudioTooLarge(ToolError):
    """Input over `MAX_INPUT_BYTES` (API: 413)."""


class NoSpeech(ToolError):
    """The VAD gate found no speech (API: 422 "no speech detected")."""


class VoiceUnavailable(ToolError):
    """A backend is not ready (missing library, model, key or binary; API: 503)."""


@dataclass(frozen=True)
class WavInfo:
    pcm: bytes
    sample_rate: int
    channels: int
    sample_width: int
    comment: str = ""


def read_wav(data: bytes) -> WavInfo:
    """Parse RIFF/WAVE chunks; oversized `data`/RIFF sizes (streamed WAVs) are clamped."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise AudioError("not a WAV file (missing RIFF/WAVE header)")
    pos, fmt, pcm, comment = 12, None, None, ""
    while pos + 8 <= len(data):
        cid, size = data[pos : pos + 4], struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        body = data[pos + 8 : min(len(data), pos + 8 + size)]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            pcm = body
        elif cid == b"LIST" and body[:4] == b"INFO":
            comment = _info_comment(body[4:]) or comment
        pos += 8 + size + (size & 1)
    if fmt is None or pcm is None:
        raise AudioError("WAV file has no fmt/data chunk")
    tag, channels, rate, _, _, bits = fmt
    if tag != 1 or bits not in (8, 16, 24, 32):
        raise AudioError(f"unsupported WAV encoding (format tag {tag}, {bits} bits); use PCM")
    width = bits // 8
    pcm = pcm[: len(pcm) - len(pcm) % (width * max(1, channels))]
    return WavInfo(pcm, rate, channels, width, comment)


def _info_comment(info: bytes) -> str:
    pos = 0
    while pos + 8 <= len(info):
        cid, size = info[pos : pos + 4], struct.unpack("<I", info[pos + 4 : pos + 8])[0]
        if cid == b"ICMT":
            return info[pos + 8 : pos + 8 + size].rstrip(b"\0").decode("utf-8", "replace")
        pos += 8 + size + (size & 1)
    return ""


def wav_from_pcm(pcm: bytes, sample_rate: int = SAMPLE_RATE, channels: int = 1, comment: str = "") -> bytes:
    """Wrap little-endian int16 PCM in a WAV container. An optional `ICMT` comment goes in a
    LIST/INFO chunk *before* `data`, so streaming readers (ffmpeg on a pipe) still see it."""
    pcm = pcm[: len(pcm) - len(pcm) % (2 * channels)]
    fmt = struct.pack("<HHIIHH", 1, channels, sample_rate, sample_rate * channels * 2, channels * 2, 16)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if comment:
        text = comment.encode("utf-8") + b"\0"
        if len(text) & 1:
            text += b"\0"
        info = b"INFO" + b"ICMT" + struct.pack("<I", len(text)) + text
        chunks += b"LIST" + struct.pack("<I", len(info)) + info
    chunks += b"data" + struct.pack("<I", len(pcm)) + pcm
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def _is_normalized(data: bytes) -> bool:
    try:
        info = read_wav(data)
    except AudioError:
        return False
    return info.sample_rate == SAMPLE_RATE and info.channels == 1 and info.sample_width == 2


async def normalize(data: bytes, mime: str | None = None) -> bytes:
    """Any audio → WAV 16 kHz mono int16 (clean header, `ICMT` comment kept)."""
    if len(data) > MAX_INPUT_BYTES:
        raise AudioTooLarge(f"audio is {len(data)} bytes; the limit is {MAX_INPUT_BYTES} bytes (25 MB)")
    if not data:
        raise AudioError("empty audio upload")
    if (mime or "").split(";")[0].strip() in _WAV_TYPES | {""} and _is_normalized(data):
        info = read_wav(data)
        return wav_from_pcm(info.pcm, SAMPLE_RATE, 1, info.comment)
    out = await run_ffmpeg(data, ("-i", "pipe:0", *FFMPEG_ARGS))
    info = read_wav(out)
    return wav_from_pcm(info.pcm, info.sample_rate, info.channels, info.comment)


async def transcode(data: bytes, fmt: str) -> tuple[bytes, str]:
    """Re-encode audio to `wav` | `mp3` | `opus` (Ogg). Returns `(bytes, media_type)`."""
    if fmt not in FORMATS:
        raise AudioError(f"format must be one of {', '.join(FORMATS)}")
    args, media_type = FORMATS[fmt]
    return await run_ffmpeg(data, ("-i", "pipe:0", *args, "pipe:1")), media_type


async def run_ffmpeg(data: bytes, args: tuple[str, ...], timeout: float = FFMPEG_TIMEOUT_S) -> bytes:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise VoiceUnavailable("ffmpeg is not installed; install it (e.g. `apt install ffmpeg`)")
    proc = await asyncio.create_subprocess_exec(
        exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(data), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise AudioError(f"ffmpeg timed out after {timeout:.0f}s") from None
    if proc.returncode != 0 or not out:
        first = err.decode("utf-8", "replace").strip().splitlines()
        raise AudioError(f"could not decode audio ({first[-1][:200] if first else 'ffmpeg failed'})")
    return out


def _samples(pcm: bytes) -> array[int]:
    a = array("h")
    a.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    return a


def rms(pcm16: bytes) -> float:
    """Normalised RMS (0..1) of little-endian int16 PCM."""
    if len(pcm16) < 2:
        return 0.0
    try:
        import numpy as np

        x = np.frombuffer(pcm16[: len(pcm16) - len(pcm16) % 2], dtype="<i2").astype("float64")
        return float(np.sqrt(np.mean(x * x)) / 32768.0)
    except ImportError:
        s = _samples(pcm16)
        return math.sqrt(sum(v * v for v in s) / len(s)) / 32768.0


def duration_s(wav: bytes) -> float:
    info = read_wav(wav)
    frames = len(info.pcm) // (info.sample_width * max(1, info.channels))
    return frames / info.sample_rate if info.sample_rate else 0.0


def concat_wavs(wavs: list[bytes]) -> bytes:
    """Concatenate WAVs with the same format (the first one's rate/channels win)."""
    infos = [read_wav(w) for w in wavs]
    first = infos[0]
    for info in infos[1:]:
        if (info.sample_rate, info.channels, info.sample_width) != (
            first.sample_rate,
            first.channels,
            first.sample_width,
        ):
            raise AudioError("cannot concatenate WAV chunks with different formats")
    comment = " ".join(i.comment for i in infos if i.comment)
    return wav_from_pcm(b"".join(i.pcm for i in infos), first.sample_rate, first.channels, comment)
