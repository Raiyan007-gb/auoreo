"""Turn tool-produced images into chat messages, and shape image parts per request.

Tool messages carry text only (several providers reject images in `role: tool`), so a
round's images travel in ONE follow-up `user` message placed after all of that round's
tool messages, each image preceded by `<tool-image call_id="…">note</tool-image>`. When the
model cannot see images the tool text gets `[image omitted: <note>]` instead — images are
never dropped silently. At request time `prepare_messages` enforces the image cap
(oldest stripped first) and degrades images to placeholders for text-only models.
"""

from __future__ import annotations

from typing import Any

from auoreo.gateway.types import ContentPart, count_images
from auoreo.tools.context import ToolResult

MAX_IMAGES_PER_REQUEST = 100
CAP_NOTE = "[image removed: request image cap of {limit} reached; oldest images are dropped first]"


def placeholder(note: str) -> str:
    return f"[image omitted: {note or 'image'}]"


def tool_messages(call_id: str, result: ToolResult, vision: bool) -> tuple[dict[str, Any], list[ContentPart]]:
    """The tool message for one result, plus the image parts it contributes to the
    round's follow-up user message (empty when the model has no vision)."""
    content = result.content
    if not result.images:
        return {"role": "tool", "tool_call_id": call_id, "content": content}, []
    if not vision:
        notes = "\n".join(placeholder(img.note) for img in result.images)
        return {"role": "tool", "tool_call_id": call_id, "content": f"{content}\n{notes}".strip()}, []
    n = len(result.images)
    content = f"{content}\n[{n} image{'s' if n != 1 else ''} attached in the next message]".strip()
    parts: list[ContentPart] = []
    for img in result.images:
        parts.append({"type": "text", "text": f'<tool-image call_id="{call_id}">{img.note}</tool-image>'})
        parts.append(img.part())
    return {"role": "tool", "tool_call_id": call_id, "content": content}, parts


def image_message(parts: list[ContentPart]) -> dict[str, Any]:
    return {"role": "user", "content": parts}


def prepare_messages(
    messages: list[dict[str, Any]], vision: bool, limit: int = MAX_IMAGES_PER_REQUEST
) -> list[dict[str, Any]]:
    """Copy of `messages` safe to send: image parts replaced by placeholders when the model
    is text-only, and only the newest `limit` images kept otherwise."""
    total = sum(count_images(m.get("content")) for m in messages)
    drop = total if not vision else max(0, total - limit)
    if drop == 0:
        return messages
    text = placeholder("model has no vision") if not vision else CAP_NOTE.format(limit=limit)
    out: list[dict[str, Any]] = []
    for m in messages:
        content = m.get("content")
        if drop == 0 or not isinstance(content, list) or not count_images(content):
            out.append(m)
            continue
        parts: list[ContentPart] = []
        for p in content:
            if drop and isinstance(p, dict) and p.get("type") == "image_url":
                drop -= 1
                parts.append({"type": "text", "text": text})
            else:
                parts.append(p)
        out.append({**m, "content": parts})
    return out
