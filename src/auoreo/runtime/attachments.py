"""I5: `auoreo run -f` attachments as message content (spec §18 "-f file, 10 MiB cap").

- text files (UTF-8, no NUL) are inlined as `<attachment path="…">` blocks (as before);
- images (png/jpeg/gif/webp, sniffed by magic bytes) become OpenAI-style `image_url` parts
  with a `data:` URL on the turn's user message — the request shaping later degrades them
  to placeholders for text-only models (`runtime/multimodal.py`);
- other binaries up to `i5.attachments.max_inline_binary_bytes` travel base64-encoded in an
  `<attachment encoding="base64">` block; larger ones are named, not inlined.

Caps (per file, per run, image count) come from `settings.i5.attachments`; exceeding one is an
`AttachmentError` (the CLI exits 2) — nothing is silently truncated.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auoreo.core.settings_i5 import AttachmentSettings

MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class AttachmentError(ValueError):
    """A missing file or a cap exceeded; the message names the file and the cap."""


@dataclass
class Composed:
    text: str
    parts: list[dict[str, Any]] = field(default_factory=list)
    """`image_url` content parts for the user message (empty for text-only runs)."""


def image_mime(data: bytes) -> str | None:
    for magic, mime in MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _text(data: bytes) -> str | None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return None if "\x00" in text else text


def compose(prompt: str, files: list[Path], cfg: AttachmentSettings | None = None) -> Composed:
    cfg = cfg or AttachmentSettings()
    blocks = [prompt] if prompt else []
    parts: list[dict[str, Any]] = []
    total = 0
    for f in files:
        if not f.is_file():
            raise AttachmentError(f"attachment not found: {f}")
        size = f.stat().st_size
        if size > cfg.max_file_bytes:
            cap = f"{cfg.max_file_bytes / 1048576:g} MiB"
            raise AttachmentError(f"attachment {f} is {size} bytes; the cap is {cap} per file")
        total += size
        if total > cfg.max_total_bytes:
            raise AttachmentError(f"attachments exceed {cfg.max_total_bytes} bytes in total")
        data = f.read_bytes()
        if (text := _text(data)) is not None:
            blocks.append(f'<attachment path="{f}">\n{text}\n</attachment>')
        elif (mime := image_mime(data)) is not None:
            if len(parts) >= cfg.max_images:
                raise AttachmentError(f"too many images: the cap is {cfg.max_images} per run")
            url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
            parts.append({"type": "image_url", "image_url": {"url": url}})
            blocks.append(
                f'<attachment path="{f}" bytes="{size}" type="{mime}">[image attached]</attachment>'
            )
        elif size <= cfg.max_inline_binary_bytes:
            mime = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            b64 = base64.b64encode(data).decode()
            head = f'<attachment path="{f}" bytes="{size}" type="{mime}" encoding="base64">'
            blocks.append(f"{head}\n{b64}\n</attachment>")
        else:
            inline_cap = cfg.max_inline_binary_bytes
            blocks.append(
                f'<attachment path="{f}" bytes="{size}">[binary file over the {inline_cap}-byte inline cap; '
                "not inlined]</attachment>"
            )
    return Composed("\n\n".join(blocks), parts)


def user_message(
    text: str, parts: tuple[dict[str, Any], ...] | list[dict[str, Any]] | None
) -> dict[str, Any]:
    """The turn's user message: plain text, or text + image parts when attachments carry images."""
    if not parts:
        return {"role": "user", "content": text}
    return {"role": "user", "content": [{"type": "text", "text": text}, *parts]}


def flatten(content: Any) -> str | None:
    """List content (text + image parts) as text for wire models that carry a string."""
    if content is None or isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    out = []
    for p in content:
        if isinstance(p, dict) and p.get("type") == "text":
            out.append(str(p.get("text", "")))
        elif isinstance(p, dict) and p.get("type") == "image_url":
            out.append("[image]")
    return "\n".join(out)
