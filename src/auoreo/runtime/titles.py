"""Session titles: one small aux model call after the first completed turn (spec M3c)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from auoreo.gateway.aux import resolve_aux_model
from auoreo.gateway.types import LLMRequest, TextDelta

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

TITLE_PROMPT = "Write a title of at most 8 words for this conversation. Reply with the title only."
MAX_TITLE = 80
FALLBACK_CHARS = 60
_QUOTES = "\"'`“”‘’«»*#"


def fallback_title(user_text: str) -> str:
    return " ".join(user_text.split())[:FALLBACK_CHARS] or "Untitled"


def clean_title(raw: str) -> str:
    title = " ".join(raw.strip().splitlines()[0].split()) if raw.strip() else ""
    if title.lower().startswith("title:"):
        title = title[6:].strip()
    title = title.strip(_QUOTES).strip().rstrip(".").strip(_QUOTES).strip()
    return title[:MAX_TITLE].rstrip()


async def _ask_model(
    services: Services, session_model: str | None, user_text: str, assistant_text: str
) -> str:
    st = services.settings
    model = resolve_aux_model(st, "title", gateway=services.gateway, main=session_model)
    request = LLMRequest(
        model=model,
        messages=(
            {"role": "system", "content": TITLE_PROMPT},
            {
                "role": "user",
                "content": f"User: {user_text[:2000]}\n\nAssistant: {assistant_text[:2000]}",
            },
        ),
        tool_choice="none",
        max_tokens=32,
        metadata={"purpose": "title"},
    )
    parts: list[str] = []
    async for item in services.gateway.stream(request):
        if isinstance(item, TextDelta):
            parts.append(item.text)
    return "".join(parts)


async def generate_title(services: Services, session_id: str, user_text: str, assistant_text: str) -> str:
    """Never raises: any failure (or an echoing fake gateway) falls back to the user text."""
    row = await services.get_session(session_id)
    if row is None:
        return ""
    try:
        reply = await _ask_model(services, row.model, user_text, assistant_text)
        title = "" if reply.lstrip().startswith("echo:") else clean_title(reply)
    except Exception as exc:
        log.info("title generation failed for %s: %s", session_id, exc)
        title = ""
    title = title or fallback_title(user_text)
    if await services.update_session(session_id, title=title) is None:
        return ""
    await services.emit(session_id, "session.title_updated", {"title": title})
    return title
