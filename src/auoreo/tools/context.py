"""Types shared between the engine and folder-defined tool handlers.

A tool handler is `async def run(args: dict, ctx: ToolContext) -> str | ToolResult`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auoreo.harness.registry import Harness


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class ToolError(Exception):
    """Raise from a handler to return a model-visible error result."""


@dataclass
class SessionState:
    """Per-session mutable state that tools may read/update."""

    read_files: dict[str, float] = field(default_factory=dict)  # path -> mtime when read
    todos: list[dict[str, Any]] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    plan_mode: bool = False
    loaded_tools: set[str] = field(default_factory=set)  # deferred tools surfaced by search
    pending_user_messages: list[str] = field(default_factory=list)


AskFn = Callable[[str, str, str], Awaitable[bool]]
"""ask(permission, pattern, detail) -> granted."""
EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]
QuestionFn = Callable[[list[dict[str, Any]]], Awaitable[dict[str, Any]]]


async def _no_ask(permission: str, pattern: str, detail: str) -> bool:
    return False


async def _no_emit(event_type: str, data: dict[str, Any]) -> None:
    return None


async def _no_question(questions: list[dict[str, Any]]) -> dict[str, Any]:
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

    def resolve_path(self, raw: str) -> Path:
        p = Path(raw).expanduser()
        return (p if p.is_absolute() else self.cwd / p).resolve()
