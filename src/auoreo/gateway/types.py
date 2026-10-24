"""Provider-neutral request/stream types and the `Gateway` protocol."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class LLMRequest:
    model: str
    messages: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...] = ()
    tool_choice: Literal["auto", "none"] = "auto"
    temperature: float | None = None
    max_tokens: int | None = None
    omit_fields: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text as produced by the model


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    estimated: bool = False


@dataclass(frozen=True)
class Finish:
    reason: str  # stop | tool_calls | length | ...


StreamItem = TextDelta | ToolCall | Usage | Finish


class Gateway(Protocol):
    name: str

    def stream(self, request: LLMRequest) -> AsyncIterator[StreamItem]: ...

    async def embed(self, model: str, texts: list[str], dims: int) -> list[list[float]]: ...

    def count_tokens(self, model: str, text: str) -> int: ...


def tool_schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """OpenAI-style function tool definition (what LiteLLM expects)."""
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }
