"""HyDE for the memory vector arm (spec §25.8, opt-in via `memory.recall_hyde`).

When a short query (< 160 chars) finds nothing through FTS, one aux call
(`metadata.purpose = "hyde"`, temperature 0, 8 s, the `small` routing group when it is
configured) writes a hypothetical fact of at most two sentences. `MemoryService.search`
embeds `query + "\\n" + doc` for KNN only: the document is never injected, never stored and
never shown. Any failure (timeout, gateway error, empty reply) returns None and search
continues with the plain query embedding. Callers that must not spend aux calls (cron,
subagents) pass `allow_aux=False`.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from auoreo.gateway.aux import resolve_aux_model
from auoreo.gateway.types import LLMRequest, TextDelta

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)
MAX_QUERY_CHARS = 160
TIMEOUT_S = 8.0
SYSTEM = (
    "You write one hypothetical memory fact that would answer the user's question, as if it "
    "were stored in their personal knowledge base. At most two short sentences. Output only "
    "the fact: no preamble, no hedging, no questions."
)


def triggered(query: str, fts_hits: int) -> bool:
    return len(query) < MAX_QUERY_CHARS and fts_hits == 0


def clip_sentences(text: str, n: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    return " ".join(parts[:n]).strip()


def model_for(services: Services) -> str:
    return resolve_aux_model(services.settings, "hyde", gateway=services.gateway)


async def _collect(services: Services, request: LLMRequest) -> str:
    parts: list[str] = []
    async for item in services.gateway.stream(request):
        if isinstance(item, TextDelta):
            parts.append(item.text)
    return "".join(parts)


async def generate(services: Services, query: str, max_tokens: int) -> str | None:
    """The hypothetical document, or None on any failure (fail-open)."""
    request = LLMRequest(
        model=model_for(services),
        messages=({"role": "system", "content": SYSTEM}, {"role": "user", "content": query}),
        tool_choice="none",
        temperature=0,
        max_tokens=max_tokens,
        metadata={"purpose": "hyde"},
    )
    try:
        text = await asyncio.wait_for(_collect(services, request), TIMEOUT_S)
    except Exception as exc:  # TimeoutError, GatewayError, anything: plain query embedding
        log.info("hyde skipped: %s", type(exc).__name__ if not str(exc) else exc)
        return None
    doc = clip_sentences(text)
    return doc or None
