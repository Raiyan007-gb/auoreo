"""Deterministic gateway for tests and keyless demos.

Behaviour per call:
1. If scripted responses were queued, pop the next one.
2. Else if the latest user message starts with `/tool <name> <json-args>`, call that tool
   once, then answer with a summary of its result on the next step.
3. Else echo the user's message.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import Finish, LLMRequest, StreamItem, TextDelta, ToolCall, Usage


@dataclass
class Scripted:
    text: str = ""
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    error: GatewayError | None = None


class FakeGateway:
    name = "fake"

    def __init__(self, script: list[Scripted] | None = None) -> None:
        self.script: deque[Scripted] = deque(script or [])
        self.requests: list[LLMRequest] = []
        self._n = 0

    def queue(self, *items: Scripted) -> None:
        self.script.extend(items)

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamItem]:
        self.requests.append(request)
        item = self.script.popleft() if self.script else self._auto(request)
        if item.error:
            raise item.error
        for word in _chunks(item.text):
            yield TextDelta(word)
        for name, args in item.tool_calls:
            self._n += 1
            yield ToolCall(id=f"call_{self._n}", name=name, arguments=json.dumps(args))
        prompt = sum(len(str(m.get("content") or "")) for m in request.messages) // 4
        yield Usage(prompt_tokens=prompt, completion_tokens=max(1, len(item.text) // 4), cost_usd=0.0)
        yield Finish("tool_calls" if item.tool_calls else "stop")

    def _auto(self, request: LLMRequest) -> Scripted:
        last = request.messages[-1] if request.messages else {}
        if last.get("role") == "tool":
            content = str(last.get("content") or "")
            return Scripted(text=f"Tool result:\n{content[:2000]}")
        user = _last_user_text(request.messages)
        if user.startswith("/tool ") and request.tool_choice != "none":
            parts = user.split(maxsplit=2)
            name = parts[1] if len(parts) > 1 else ""
            try:
                args = json.loads(parts[2]) if len(parts) > 2 else {}
            except json.JSONDecodeError:
                args = {}
            return Scripted(tool_calls=[(name, args)])
        return Scripted(text=f"echo: {user}")

    async def embed(self, model: str, texts: list[str], dims: int) -> list[list[float]]:
        return [hash_embedding(t, dims) for t in texts]

    def count_tokens(self, model: str, text: str) -> int:
        return max(1, len(text) // 4)


def _chunks(text: str, size: int = 16) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _last_user_text(messages: tuple[dict[str, Any], ...]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = str(m.get("content") or "")
            # strip the per-turn <context> wrapper added by the runner
            if content.startswith("<context>") and "</context>" in content:
                content = content.split("</context>", 1)[1]
            return content.strip()
    return ""


def hash_embedding(text: str, dims: int) -> list[float]:
    """Bag-of-words hashed embedding: similar texts share dimensions. Deterministic."""
    vec = [0.0] * dims
    for tok in text.lower().split():
        h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
        vec[h % dims] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]
