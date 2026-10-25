"""Provider-neutral request/stream types and the `Gateway` protocol."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

ContentPart = dict[str, Any]
"""OpenAI-style content part: `{"type": "text", "text": …}` or
`{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,…"}}`.
A message `content` is either a string or a list of these (passed through unchanged)."""

IMAGE_TOKEN_ESTIMATE = 1600
"""Rough per-image prompt cost; base64 length says nothing about vision tokens."""
_DATA_URL = re.compile(r"data:image/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=]+")


@dataclass(frozen=True)
class ImagePart:
    """An image produced by a tool, ready to send as a data URL."""

    media_type: str  # image/jpeg | image/png | image/gif | image/webp
    data_b64: str
    note: str = ""  # short human description (source, size) used in placeholders

    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{self.data_b64}"

    def part(self) -> ContentPart:
        return {"type": "image_url", "image_url": {"url": self.data_url()}}


def text_of(content: Any) -> str:
    """The text of a message `content` (string, or the joined text parts of a list)."""
    if isinstance(content, list):
        return "\n".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content or "")


def count_images(content: Any) -> int:
    if not isinstance(content, list):
        return 0
    return sum(1 for p in content if isinstance(p, dict) and p.get("type") == "image_url")


def shrink_data_urls(text: str) -> str:
    """Replace inline base64 images with a fixed-size marker so length-based token
    estimates charge ~IMAGE_TOKEN_ESTIMATE per image instead of the base64 size."""
    return _DATA_URL.sub("<image>" + "#" * (IMAGE_TOKEN_ESTIMATE * 4), text)


def prompt_chars(messages: tuple[dict[str, Any], ...]) -> int:
    """Character size of a prompt for usage estimates (images at their token estimate)."""
    total = 0
    for m in messages:
        content = m.get("content")
        total += len(text_of(content)) + count_images(content) * IMAGE_TOKEN_ESTIMATE * 4
    return total


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

    def context_window(self, model: str) -> int:
        """Max input tokens for a model id or routing group (spec §11.9 S0)."""
        ...

    def price(self, model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
        """Estimated USD cost of one call, or None when the model has no known pricing."""
        ...

    def supports_vision(self, model: str) -> bool:
        """Whether `model` (id or routing group) accepts `image_url` content parts."""
        ...


def tool_schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """OpenAI-style function tool definition (what LiteLLM expects)."""
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }
