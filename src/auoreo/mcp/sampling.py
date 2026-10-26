"""MCP sampling → one aux gateway call (spec §2.5 P1, §26.14).

A server that opts in (`sampling: {enabled: true}` in `mcp/<name>.yaml`) may send
`sampling/createMessage` (a legacy back-channel request, or an `InputRequiredResult` round
on 2026-07-28 connections — the SDK routes both to this callback). Each request:

1. is attributed to the session whose tool call is running (else refused);
2. is rate-limited (`tool_engine.sampling_rpm` per server);
3. passes permission `mcp_sampling` (pattern = server name): `allow` rules skip the prompt,
   `deny` refuses, otherwise the first request per session is approved by a human
   (non-interactive runs refuse);
4. is counted against the running tool call's iteration budget (`i6.mcp.sampling_iterations`,
   3): the last allowed pass runs with `toolChoice: none` (no tools), later ones are refused;
5. runs as an aux call (`metadata.purpose = "sampling"`, `max_tokens` capped by the manifest)
   on the model `modelPreferences` select (`sampling_tools.select_model`, never the main
   model by hint); `params.tools` are passed through and tool calls come back as
   `ToolUseContent` (`CreateMessageResultWithTools`, `stop_reason: toolUse`); charged to the
   usage ledger and audited `mcp.sampling`.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.types import CreateMessageResult, CreateMessageResultWithTools, ErrorData

from auoreo.gateway.types import Finish, LLMRequest, TextDelta, ToolCall, Usage
from auoreo.mcp.sampling_tools import choice_of, select_model, to_messages, to_result, to_tools

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.harness.manifests import McpServerManifest

log = logging.getLogger(__name__)
REJECTED = -1  # MCP convention for "user rejected sampling request"
PERMISSION = "mcp_sampling"


class SamplingHandler:
    def __init__(
        self,
        services: Services,
        manifest: McpServerManifest,
        fallback: Callable[[], str | None],
        call_key: Callable[[], str | None] = lambda: None,
    ):
        self.s = services
        self.m = manifest
        self.fallback = fallback
        self.call_key = call_key
        self.approved: set[str] = set()
        self._recent: deque[float] = deque()
        self._passes: dict[str, int] = {}

    def _iteration(self, sid: str) -> tuple[int, int]:
        """(this pass number, cap) for the running tool call (keyed per call, else per session)."""
        key = self.call_key() or f"session:{sid}"
        if len(self._passes) > 512:  # old calls never come back; keep the map bounded
            self._passes.clear()
        self._passes[key] = self._passes.get(key, 0) + 1
        return self._passes[key], self.s.settings.i6.mcp.sampling_iterations

    def _rate_ok(self) -> bool:
        now = time.monotonic()
        while self._recent and now - self._recent[0] > 60:
            self._recent.popleft()
        if len(self._recent) >= self.s.settings.tool_engine.sampling_rpm:
            return False
        self._recent.append(now)
        return True

    async def _approve(self, sid: str, preview: str) -> str | None:
        """None = approved; else the refusal message."""
        if sid in self.approved:
            return None
        coord = self.s.coordinator
        try:
            interactive = (await coord.runtime(sid)).interactive
        except KeyError:  # no such session row: nobody can approve
            interactive = False
        engine = coord.engine_for(sid)
        server = self.m.name
        action = "ask"
        if engine is not None:
            action = engine.evaluate(PERMISSION, server, "ask", interactive=interactive).action
        if action == "deny":
            return f"sampling for '{server}' is denied by policy"
        if action == "ask":
            if not interactive:
                return f"sampling for '{server}' needs approval but this run is non-interactive"
            outcome = await self.s.approvals.request(
                sid, f"mcp__{server}", PERMISSION, server, preview[:2000], risk="medium"
            )
            if not outcome.granted:
                return "the user rejected this sampling request"
            if outcome.reply == "always" and engine is not None:
                engine.grant_always(PERMISSION, server)
        self.approved.add(sid)
        return None

    async def __call__(
        self, context: Any, params: Any
    ) -> CreateMessageResult | CreateMessageResultWithTools | ErrorData:
        from auoreo.tools.context import CURRENT_SESSION

        sid = CURRENT_SESSION.get() or self.fallback()
        if sid is None:
            return ErrorData(code=REJECTED, message="no AUOREO session is waiting on this server")
        if not self._rate_ok():
            return ErrorData(code=REJECTED, message=f"sampling rate limit reached for '{self.m.name}'")
        messages = to_messages(params)
        if (why := await self._approve(sid, "\n".join(str(m["content"]) for m in messages))) is not None:
            await self._audit(sid, "deny", why, None, 0)
            return ErrorData(code=REJECTED, message=why)
        n, cap = self._iteration(sid)
        if n > cap:
            why = f"sampling iteration limit ({cap}) reached for this tool call"
            await self._audit(sid, "deny", why, None, 0)
            return ErrorData(code=REJECTED, message=why)
        cfg, st = self.m.sampling, self.s.settings
        model = cfg.model or select_model(st, self.s.gateway, getattr(params, "model_preferences", None))
        tools = to_tools(params)
        with_tools = bool(tools) or getattr(params, "tool_choice", None) is not None
        last = n >= cap
        request = LLMRequest(
            model=model,
            messages=messages,
            tools=() if last else tools,
            tool_choice="none" if last or not tools else choice_of(params),  # type: ignore[arg-type]
            temperature=params.temperature,
            max_tokens=min(int(params.max_tokens), cfg.max_tokens),
            metadata={"purpose": "sampling", "session_id": sid, "server": self.m.name, "pass": n},
        )
        parts: list[str] = []
        calls: list[ToolCall] = []
        usage: Usage | None = None
        finish: str | None = None
        try:
            async for item in self.s.gateway.stream(request):
                if isinstance(item, TextDelta):
                    parts.append(item.text)
                elif isinstance(item, ToolCall) and not last:
                    calls.append(item)
                elif isinstance(item, Usage):
                    usage = item
                elif isinstance(item, Finish):
                    finish = item.reason
        except Exception as exc:
            log.info("MCP sampling for %s failed: %s", self.m.name, exc)
            return ErrorData(code=REJECTED, message=f"sampling failed: {type(exc).__name__}")
        tokens = (usage.prompt_tokens + usage.completion_tokens) if usage else 0
        await self._charge(sid, model, usage)
        await self._audit(sid, "allow", "sampling", model, tokens)
        return to_result("".join(parts), calls, model, with_tools, finish)

    async def _charge(self, sid: str, model: str, usage: Usage | None) -> None:
        ledger = getattr(getattr(self.s.coordinator, "runner", None), "usage", None)
        if ledger is None or usage is None:
            return
        try:
            await ledger.record(sid, "mcp_sampling", model, usage)
        except Exception:
            log.warning("could not record MCP sampling spend", exc_info=True)

    async def _audit(self, sid: str, decision: str, reason: str, model: str | None, tokens: int) -> None:
        await self.s.audit.record(
            actor="mcp",
            action="mcp.sampling",
            session_id=sid,
            decision=decision,
            reason=reason[:300],
            detail={"server": self.m.name, "model": model, "tokens": tokens},
        )
