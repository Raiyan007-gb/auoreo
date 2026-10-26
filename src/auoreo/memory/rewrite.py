"""`rewrite_memory_query` (spec §11.6): one aux call that turns a conversational turn into a
standalone recall query before memory search. Opt-in via `compaction.rewrite_memory_query`.

The call uses `metadata.purpose = "memory_query"`, temperature 0, ≤ 96 output tokens and an
8 s timeout on the aux model. The reply must be a single grounded question of ≤ 320 chars;
anything else (empty, too long, multi-line, a refusal, an error, a timeout) falls back to
the raw text. Trivial prompts never spend the call.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from auoreo.gateway.aux import resolve_aux_model
from auoreo.gateway.types import LLMRequest, TextDelta
from auoreo.memory.service import is_trivial

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
MAX_TOKENS = 96
MAX_CHARS = 320
TIMEOUT_S = 8.0
CONTEXT_CHARS = 600
SYSTEM = (
    "Rewrite the user's latest message as ONE standalone search question for their personal "
    "memory store. Resolve pronouns and references using the recent conversation. Use only "
    "facts present in the conversation; never answer the question. Output only the question "
    "on a single line."
)


def _recent(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for m in messages:
        content = m.get("content")
        if m.get("role") in ("user", "assistant") and isinstance(content, str) and content.strip():
            text = content.split("</context>", 1)[-1].strip()  # drop the runner's per-turn wrapper
            lines.append(f"{m['role']}: {text[:CONTEXT_CHARS]}")
    return "\n".join(lines)


def accept(raw: str, fallback: str) -> str:
    """The rewrite when it is a single line of ≤ MAX_CHARS, else `fallback`."""
    text = raw.strip().strip('"').strip()
    if not text or "\n" in text or len(text) > MAX_CHARS:
        return fallback
    return text


async def _collect(services: Services, request: LLMRequest) -> str:
    parts: list[str] = []
    async for item in services.gateway.stream(request):
        if isinstance(item, TextDelta):
            parts.append(item.text)
    return "".join(parts)


async def rewrite_query(services: Services, text: str, recent: list[dict[str, Any]]) -> str:
    if is_trivial(text):
        return text
    convo = _recent(recent)
    user = text[:2000]
    if convo:
        user = f"<conversation>\n{convo}\n</conversation>\n\n<latest>{user}</latest>"
    request = LLMRequest(
        model=resolve_aux_model(services.settings, "memory_query", gateway=services.gateway),
        messages=({"role": "system", "content": SYSTEM}, {"role": "user", "content": user}),
        tool_choice="none",
        temperature=0,
        max_tokens=MAX_TOKENS,
        metadata={"purpose": "memory_query"},
    )
    try:
        raw = await asyncio.wait_for(_collect(services, request), TIMEOUT_S)
    except Exception as exc:  # TimeoutError, GatewayError, anything: the raw query
        log.info("memory query rewrite skipped: %s", type(exc).__name__ if not str(exc) else exc)
        return text
    return accept(raw, text)


__all__ = ["MAX_CHARS", "MAX_TOKENS", "accept", "rewrite_query"]
