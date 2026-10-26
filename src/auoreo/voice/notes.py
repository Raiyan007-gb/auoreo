"""I7 voice-note delivery (spec §13.4 L615, §25.3): the `MEDIA:` envelope.

A reply (or a job/webhook result) may carry lines like

    MEDIA:{"path": "/…/sessions/<id>/audio/3.wav", "mime": "audio/wav", "duration_s": 2.4}

(`MEDIA:/abs/path.wav` also works). The `speak` tool prints such a line for the file it wrote.
Before a delivery provider is called, `attach()` strips the lines from the text and turns each
into `meta["media"]` entries the provider can upload:

- Opus in Ogg (`audio/ogg`, `.ogg`) for chat apps that render voice bubbles (telegram, discord,
  matrix, feishu, whatsapp, signal); MP3 (`audio/mpeg`) everywhere else (email, …).
- Re-encoding uses the `ffmpeg` binary; without it the error says so (no silent fallback).
- 16 MiB cap on the delivered file.
- Only audio files under `<home>/sessions/` are accepted, so a reply can never smuggle an
  arbitrary file (secrets.db, keys, …) out through a delivery.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from auoreo.voice import audio

PREFIX = "MEDIA:"
LINE_RE = re.compile(r"(?m)^[ \t]*MEDIA:[ \t]*(\S.*?)[ \t]*$")
MAX_BYTES = 16 * 2**20
MAX_ITEMS = 4
OPUS_PROVIDERS = frozenset({"telegram", "discord", "matrix", "feishu", "whatsapp", "signal"})
AUDIO_EXT = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
}
TARGETS: dict[str, tuple[str, str, str]] = {
    "opus": ("audio/ogg", ".ogg", "opus"),
    "mp3": ("audio/mpeg", ".mp3", "mp3"),
}


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaRef:
    path: str
    mime: str
    duration_s: float | None = None
    kind: str = "voice"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def envelope(path: str | Path, mime: str, duration_s: float | None = None) -> str:
    body: dict[str, Any] = {"path": str(path), "mime": mime}
    if duration_s is not None:
        body["duration_s"] = round(float(duration_s), 2)
    return PREFIX + json.dumps(body, ensure_ascii=False)


def _parse(raw: str) -> MediaRef | None:
    if raw.startswith("{"):
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(body, dict) or not isinstance(body.get("path"), str):
            return None
        dur = body.get("duration_s")
        mime = str(body.get("mime") or AUDIO_EXT.get(Path(body["path"]).suffix.lower(), ""))
        return MediaRef(body["path"], mime, float(dur) if isinstance(dur, int | float) else None)
    return MediaRef(raw, AUDIO_EXT.get(Path(raw).suffix.lower(), ""))


def extract(text: str) -> tuple[str, list[MediaRef]]:
    """(text without the MEDIA lines, the parsed references — at most `MAX_ITEMS`)."""
    refs: list[MediaRef] = []

    def take(m: re.Match[str]) -> str:
        ref = _parse(m.group(1))
        if ref is None:
            return m.group(0)
        if len(refs) < MAX_ITEMS:
            refs.append(ref)
        return ""

    clean = LINE_RE.sub(take, text or "")
    return re.sub(r"\n{3,}", "\n\n", clean).strip(), refs


def target_format(provider: str) -> str:
    return "opus" if provider in OPUS_PROVIDERS else "mp3"


def confine(ref: MediaRef, home: Path) -> Path:
    """The real audio file behind `ref`, which must live under `<home>/sessions/`."""
    root = (home / "sessions").resolve()
    try:
        path = Path(ref.path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise MediaError(f"voice note not found: {Path(ref.path).name}") from exc
    if not path.is_relative_to(root) or not path.is_file():
        raise MediaError("voice notes must be files under the AUOREO sessions folder")
    if path.suffix.lower() not in AUDIO_EXT:
        raise MediaError(f"not an audio file: {path.name}")
    return path


async def _probe_duration(path: Path) -> float | None:
    exe = shutil.which("ffprobe")
    if exe is None:
        return None
    proc = await asyncio.create_subprocess_exec(
        exe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 10)
        return round(float(out.decode().strip()), 2)
    except (TimeoutError, ValueError):
        return None


async def prepare(ref: MediaRef, provider: str, home: Path) -> MediaRef:
    """Re-encode (when needed) for `provider`; returns the file to upload."""
    src = confine(ref, home)
    size = src.stat().st_size
    if size > audio.MAX_INPUT_BYTES:
        raise MediaError(f"voice note source is {size} bytes (limit {audio.MAX_INPUT_BYTES})")
    mime, ext, fmt = TARGETS[target_format(provider)]
    data = await asyncio.to_thread(src.read_bytes)
    duration = ref.duration_s
    if duration is None and src.suffix.lower() == ".wav":
        try:
            duration = round(audio.duration_s(data), 2)
        except audio.AudioError:
            duration = None
    if AUDIO_EXT[src.suffix.lower()] == mime and (fmt != "opus" or src.suffix.lower() in {".ogg", ".opus"}):
        out_path = src
    else:
        try:
            encoded, _ = await audio.transcode(data, fmt)
        except audio.VoiceUnavailable as exc:
            raise MediaError(f"cannot encode the voice note: {exc}") from exc
        except audio.AudioError as exc:
            raise MediaError(f"cannot encode the voice note: {exc}") from exc
        out_path = src.parent / "voice" / (src.stem + ext)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(out_path.write_bytes, encoded)
    if out_path.stat().st_size > MAX_BYTES:
        raise MediaError(f"voice note is {out_path.stat().st_size} bytes; the cap is {MAX_BYTES} (16 MiB)")
    if duration is None:
        duration = await _probe_duration(out_path)
    return MediaRef(str(out_path), mime, duration)


async def attach(text: str, provider: str, home: Path) -> tuple[str, list[dict[str, Any]], list[str]]:
    """(clean text, `meta["media"]` entries, honest errors). No MEDIA lines → text unchanged."""
    if PREFIX not in (text or ""):
        return text, [], []
    clean, refs = extract(text)
    media: list[dict[str, Any]] = []
    errors: list[str] = []
    for ref in refs:
        try:
            media.append((await prepare(ref, provider, home)).as_dict())
        except MediaError as exc:
            errors.append(str(exc))
    return clean, media, errors


def with_errors(text: str, errors: list[str]) -> str:
    """Append a short note so the recipient knows a voice note was dropped (and why)."""
    if not errors:
        return text
    note = "; ".join(errors)
    return f"{text}\n\n(voice note not delivered: {note})".strip()
