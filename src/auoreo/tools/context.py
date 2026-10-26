"""Types shared between the engine and folder-defined tool handlers.

A tool handler is `async def run(args: dict, ctx: ToolContext) -> str | ToolResult`.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auoreo.gateway.types import ImagePart
from auoreo.schema.events import ToolProgress

if TYPE_CHECKING:
    from auoreo.harness.registry import Harness


CURRENT_SESSION: ContextVar[str | None] = ContextVar("auoreo_current_session", default=None)
"""Session id of the tool call running in this task; set by the executor around each
handler call so engine code without a `ToolContext` (e.g. MCP elicitation) can find it."""


@dataclass(frozen=True)
class ExecPolicy:
    """Sandbox policy for the tool call running in this task (spec §19.8). Set by the
    executor from the manifest (`sandbox_mode`, `net`) plus a one-call escalation grant."""

    mode: str = "workspace-write"
    net: str = "none"
    grant_id: str | None = None


CURRENT_EXEC: ContextVar[ExecPolicy | None] = ContextVar("auoreo_current_exec", default=None)


@dataclass
class CallInfo:
    """The tool call running in this task (set by the executor): who `ctx.progress()` reports
    for, and its rate-limit state."""

    call_id: str
    tool: str
    interval_s: float = 0.5
    last: float = 0.0


CURRENT_CALL: ContextVar[CallInfo | None] = ContextVar("auoreo_current_call", default=None)
_PHASES = frozenset({"starting", "running", "waiting", "finishing"})


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    images: list[ImagePart] = field(default_factory=list)
    """Images for the model; the runner sends them as a follow-up user message when the
    session model supports vision, else appends `[image omitted: <note>]` placeholders."""


class ToolError(Exception):
    """Raise from a handler to return a model-visible error result."""


@dataclass
class SessionState:
    """Per-session mutable state that tools may read/update."""

    read_files: dict[str, float] = field(default_factory=dict)  # path -> mtime when read
    todos: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    plan_mode: bool = False
    plan_verified: str | None = None  # plan hash `verify_plan` last passed
    loaded_tools: set[str] = field(default_factory=set)  # deferred tools surfaced by search
    pending_user_messages: list[str] = field(default_factory=list)
    sandbox_note_shown: bool = False  # `[sandbox: not enforced …]` told once per session
    environment: str | None = None  # W2-A: `use_environment` override (settings.environments key)


TOOLS_LOADED_PREFIX = "loaded (callable from the next step):"
"""First line of a `tool_search` result; the `- name:` lines after it are the loaded tools."""


def loaded_from_history(history: list[dict[str, Any]]) -> set[str]:
    """Deferred tools surfaced by earlier `tool_search` calls in a persisted transcript, so a
    session reopened in another process (`auoreo run --session`, a restart) keeps them."""
    names: set[str] = set()
    for msg in history:
        content = msg.get("content") if msg.get("role") == "tool" else None
        if not isinstance(content, str) or not content.startswith(TOOLS_LOADED_PREFIX):
            continue
        for line in content.splitlines()[1:]:
            if line.startswith("- ") and ":" in line:
                names.add(line[2:].split(":", 1)[0].strip())
    return names


def restore_state(state: SessionState, history: list[dict[str, Any]]) -> None:
    """Rebuild cross-process session state from a persisted transcript: tools loaded by
    `tool_search`, the last `update_plan` steps, the last `verify_plan` verdict, plan mode."""
    state.loaded_tools.update(loaded_from_history(history))
    calls: dict[str, tuple[str, str]] = {}
    for msg in history:
        tool_calls = msg.get("tool_calls") if msg.get("role") == "assistant" else None
        for tc in tool_calls or []:
            fn = tc.get("function") or {}
            calls[str(tc.get("id"))] = (str(fn.get("name", "")), str(fn.get("arguments") or "{}"))
        if msg.get("role") != "tool" or not isinstance(msg.get("content"), str):
            continue
        name, raw = calls.get(str(msg.get("tool_call_id")), ("", "{}"))
        _RESTORERS.get(name, _noop)(state, raw, msg["content"])


def _noop(state: SessionState, raw: str, content: str) -> None:
    return None


def _restore_plan(state: SessionState, raw: str, content: str) -> None:
    if content.startswith("plan updated:"):
        with contextlib.suppress(ValueError, TypeError):
            steps = json.loads(raw).get("steps")
            if isinstance(steps, list):
                state.plan = steps


def _restore_verify(state: SessionState, raw: str, content: str) -> None:
    last = content.rstrip().splitlines()[-1] if content.strip() else ""
    passed = content.startswith("verify_plan: PASS") and last.startswith("plan hash: ")
    state.plan_verified = last.removeprefix("plan hash: ").strip() if passed else None


def _restore_mode(on: bool, prefix: str) -> Callable[[SessionState, str, str], None]:
    def apply(state: SessionState, raw: str, content: str) -> None:
        if content.startswith(prefix):
            state.plan_mode = on

    return apply


_RESTORERS: dict[str, Callable[[SessionState, str, str], None]] = {
    "update_plan": _restore_plan,
    "verify_plan": _restore_verify,
    "enter_plan_mode": _restore_mode(True, "Plan mode is on"),
    "exit_plan_mode": _restore_mode(False, "Plan approved"),
}


AskFn = Callable[[str, str, str], Awaitable[bool]]
"""ask(permission, pattern, detail) -> granted."""
EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]
QuestionFn = Callable[..., Awaitable[dict[str, Any]]]
"""`question(questions, timeout_s=None)` → `{responses, timed_out}`."""


async def _no_ask(permission: str, pattern: str, detail: str) -> bool:
    return False


async def _no_emit(event_type: str, data: dict[str, Any]) -> None:
    return None


async def _no_question(questions: list[dict[str, Any]], timeout_s: float | None = None) -> dict[str, Any]:
    return {"responses": [], "timed_out": True}


@dataclass
class ToolContext:
    session_id: str
    cwd: Path
    home: Path
    harness: Harness | None = None
    state: SessionState = field(default_factory=SessionState)
    interactive: bool = False
    ask: AskFn = _no_ask
    emit: EmitFn = _no_emit
    question: QuestionFn = _no_question
    services: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    """Scrubbed environment for child processes (allowlist + tool `requires_env`)."""

    async def progress(
        self,
        message: str = "",
        *,
        percent: float | None = None,
        phase: str = "running",
        cancellable: bool = False,
    ) -> bool:
        """Emit a `tool.progress` event for the running call (spec §25.7). Rate-limited per
        call; returns False when dropped (no call running, or within the interval)."""
        call = CURRENT_CALL.get()
        if call is None:
            return False
        now = time.monotonic()
        final = percent is not None and percent >= 100
        if not final and call.last and now - call.last < call.interval_s:
            return False
        call.last = now
        data = ToolProgress(
            call_id=call.call_id,
            tool=call.tool,
            phase=phase if phase in _PHASES else "running",
            message=message[:500],
            percent=None if percent is None else max(0.0, min(100.0, float(percent))),
            cancellable=cancellable,
        )
        await self.emit("tool.progress", data.model_dump(exclude_none=True, exclude={"run_id"}))
        return True

    @property
    def on_progress(self) -> Callable[..., Awaitable[bool]]:
        """`onProgress` spelling of `progress` for handlers ported from TS tools."""
        return self.progress

    @property
    def exec_policy(self) -> ExecPolicy:
        """The sandbox mode/net this call runs under (manifest + any one-call grant)."""
        return CURRENT_EXEC.get() or ExecPolicy()

    def resolve_path(self, raw: str) -> Path:
        p = Path(raw).expanduser()
        return (p if p.is_absolute() else self.cwd / p).resolve()

    def backend(self, kind: str) -> Any:
        """The selected backend instance for `kind` (see `backends/README.md`).

        Raises `ToolError` when no backend is selected or it cannot be loaded."""
        svc = self.services.get("services")
        if svc is None:
            raise ToolError(f"no services attached; backend '{kind}' unavailable")
        try:
            return svc.backend(kind)
        except (KeyError, LookupError) as exc:
            raise ToolError(str(exc)) from exc

    async def authorize(self, tool: str, permission: str, pattern: str, default: str = "ask") -> None:
        """Check a *secondary* permission from inside a handler (e.g. a URL fetched by a
        tool whose own permission is `read`). Floors and rules apply; `ask` goes to the
        human when interactive. Raises `ToolError` unless the call is allowed."""
        engine = self.services.get("engine")
        if engine is None:
            return
        decision = engine.evaluate(permission, pattern, default, interactive=self.interactive)
        if decision.action == "allow":
            return
        if decision.action == "deny":
            raise ToolError(f"permission denied for {permission} `{pattern[:200]}` ({decision.detail})")
        svc = self.services.get("services")
        if not self.interactive or svc is None:
            raise ToolError(
                f"{permission} `{pattern[:200]}` needs approval but this run is non-interactive; "
                "allow it with a permission rule"
            )
        outcome = await svc.approvals.request(self.session_id, tool, permission, pattern, pattern[:2000])
        if not outcome.granted:
            raise ToolError(f"user rejected {permission} `{pattern[:200]}`")
        if outcome.reply == "always":
            engine.grant_always(permission, pattern)
