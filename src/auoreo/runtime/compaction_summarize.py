"""S5 summarize: ONE aux call per attempt, KV-cache-aware (spec §11.9).

Request shape: the agent's own system prompt + tool schemas + the protected head messages
replayed verbatim (the same prefix the main request starts with, so a provider prompt
cache can serve it), then the middle as a capped `<transcript>`, then the compaction
instruction as the final user message. `tool_choice="none"`, temperature 0.

Retries inside one compaction:
- a timeout climbs the ladder (`compaction.timeout_ladder_s`, default 60 → 300 → 900 s);
- a prompt-too-long (`CONTEXT`) error drops the oldest API-round group of the middle and
  retries (max `compaction.ptl_max_retries`, default 3);
- `finish_reason=length` aborts: a cut-off summary must never replace the history.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from auoreo.gateway.aux import resolve_aux_model
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import Finish, LLMRequest, TextDelta, Usage, tool_schema
from auoreo.runtime.compaction_plan import drop_oldest_group, render_transcript, text_only

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.core.usage import UsageLedger
    from auoreo.runtime.runner import SessionRuntime

log = logging.getLogger(__name__)

SUMMARY_MAX_TOKENS = 8192
WRITE_NOW = "Write the compaction summary now."
SCOPE_NOTE = (
    "The messages before the <transcript> are the start of the conversation and stay "
    "verbatim; the <transcript> is the part being compacted. Summarize the transcript."
)


class SummaryError(Exception):
    """Raised by `Summarizer.summarize`; the service maps it to a CompactionError."""

    def __init__(self, reason: str, failure_class: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.failure_class = failure_class


@dataclass(frozen=True)
class Summary:
    text: str
    model: str
    retries: int
    dropped_groups: int


class Summarizer:
    def __init__(self, services: Services, ledger: UsageLedger | None = None) -> None:
        self.s = services
        self.ledger = ledger

    def _prefix(self, rt: SessionRuntime, model: str) -> tuple[str | None, tuple[dict[str, Any], ...]]:
        """The agent's system prompt + tool schemas (None/() when the agent cannot be built)."""
        from auoreo.harness.orchestrator import build_spec

        try:  # same inputs as `AgentRunner._spec`, so the prefix is byte-identical
            mode = self.s.harness.agent(rt.agent).manifest.permission_mode or self.s.settings.permission_mode
            spec = build_spec(self.s.harness, rt.agent, model, mode)
        except Exception:  # unknown agent / template error: fall back to the bare template
            return None, ()
        tools = tuple(tool_schema(t.name, t.manifest.description, t.manifest.parameters) for t in spec.tools)
        return spec.system_prompt, tools

    def _request(
        self,
        rt: SessionRuntime,
        cid: str,
        group: str,
        model: str,
        head: list[dict[str, Any]],
        middle: list[dict[str, Any]],
    ) -> LLMRequest:
        prompt = self.s.harness.snapshot.prompts.get("compaction")
        if prompt is None or prompt.section.role != "compaction":
            raise SummaryError("prompts/compaction.md (role: compaction) is missing", "config")
        system, tools = self._prefix(rt, model)
        transcript = {"role": "user", "content": f"<transcript>\n{render_transcript(middle)}\n</transcript>"}
        if system is None:
            messages: tuple[dict[str, Any], ...] = (
                {"role": "system", "content": prompt.template},
                transcript,
                {"role": "user", "content": WRITE_NOW},
            )
        else:
            instruction = f"{prompt.template}\n\n{SCOPE_NOTE}\n\n{WRITE_NOW}"
            messages = (
                {"role": "system", "content": system},
                *(text_only(m) for m in head),
                transcript,
                {"role": "user", "content": instruction},
            )
        return LLMRequest(
            model=group,
            messages=messages,
            tools=tools,
            tool_choice="none",
            temperature=0,
            max_tokens=SUMMARY_MAX_TOKENS,
            metadata={"session_id": rt.session_id, "purpose": "compaction", "compaction_id": cid},
        )

    async def _stream(self, request: LLMRequest, sid: str, cid: str) -> tuple[str, str]:
        parts: list[str] = []
        finish = "stop"
        async for item in self.s.gateway.stream(request):  # no compaction can recurse from here
            if isinstance(item, TextDelta):
                parts.append(item.text)
            elif isinstance(item, Finish):
                finish = item.reason
            elif isinstance(item, Usage) and self.ledger is not None:
                await self.ledger.record(sid, cid, request.model, item)
        return "".join(parts).strip(), finish

    async def summarize(
        self,
        rt: SessionRuntime,
        cid: str,
        model: str,
        head: list[dict[str, Any]],
        middle: list[dict[str, Any]],
    ) -> Summary:
        cfg = self.s.settings.compaction
        ladder = cfg.timeout_ladder_s
        group = resolve_aux_model(self.s.settings, "compaction", gateway=self.s.gateway, main=model)
        rung = dropped = 0
        while True:
            request = self._request(rt, cid, group, model, head, middle)
            try:
                text, finish = await asyncio.wait_for(self._stream(request, rt.session_id, cid), ladder[rung])
            except TimeoutError:
                rung += 1
                if rung >= len(ladder):
                    raise SummaryError(f"summarizer timed out after {ladder[-1]:g}s", "timeout") from None
                log.info("compaction %s: summarizer timeout, retrying with %gs", cid, ladder[rung])
                continue
            except GatewayError as exc:
                shorter = drop_oldest_group(middle) if exc.code == "CONTEXT" else None
                if shorter is not None and dropped < cfg.ptl_max_retries:
                    dropped += 1
                    middle = shorter
                    continue
                cls = "prompt_too_long" if exc.code == "CONTEXT" else "gateway"
                reason = f"summarizer call failed: {exc.code}: {exc.message[:300]}"
                raise SummaryError(reason, cls) from exc
            if finish == "length":
                reason = "summary hit the output limit (finish_reason=length); session left unchanged"
                raise SummaryError(reason, "length")
            if not text:
                raise SummaryError("summarizer returned no text", "empty")
            return Summary(text, group, rung + dropped, dropped)


__all__ = ["SUMMARY_MAX_TOKENS", "WRITE_NOW", "Summarizer", "Summary", "SummaryError"]
