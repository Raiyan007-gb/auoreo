"""Deterministic gateway for tests and keyless demos.

Behaviour per call:
1. If scripted responses were queued, pop the next one.
2. Else if the latest user message starts with `/tool <name> <json-args>`, call that tool
   once, then answer with a summary of its result on the next step.
3. Else echo the user's message (`echo: <text> [+N images]` when it carried images).

`vision` (default True) is what `supports_vision` reports; aux calls with
`metadata.purpose == "vision"` answer with a deterministic description; `"hyde"` calls answer
with a one-sentence hypothetical fact built from the query's words.
Guardian calls (`purpose == "guardian"`) pop `guardian_script`; when it is empty they
answer `$AUOREO_FAKE_GUARDIAN` (default `ESCALATE`).
Every request is kept in `requests` (with its `reasoning_effort`); `efforts` lists the effort
of each main-turn call (no `metadata.purpose`) in order.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import (
    Finish,
    LLMRequest,
    StreamItem,
    TextDelta,
    ToolCall,
    Usage,
    count_images,
    prompt_chars,
    shrink_data_urls,
    text_of,
)


@dataclass
class Scripted:
    text: str = ""
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    error: GatewayError | None = None


class FakeGateway:
    name = "fake"

    def __init__(self, script: list[Scripted] | None = None, context_window_tokens: int = 128_000) -> None:
        self.script: deque[Scripted] = deque(script or [])
        self.compaction_script: deque[Scripted] = deque()
        self.guardian_script: deque[str] = deque()
        self.requests: list[LLMRequest] = []
        self.efforts: list[str | None] = []
        self.context_window_tokens = context_window_tokens
        self.vision = True
        self._n = 0

    def queue(self, *items: Scripted) -> None:
        self.script.extend(items)

    async def stream(self, request: LLMRequest) -> AsyncIterator[StreamItem]:
        self.requests.append(request)
        if "purpose" not in request.metadata:
            self.efforts.append(request.reasoning_effort)
        if request.metadata.get("purpose") == "compaction":  # aux call never eats the main script
            item = self.compaction_script.popleft() if self.compaction_script else _summary(request)
        elif request.metadata.get("purpose") == "guardian":  # nor does the smart-mode guardian
            default = os.environ.get("AUOREO_FAKE_GUARDIAN", "ESCALATE")
            item = Scripted(text=self.guardian_script.popleft() if self.guardian_script else default)
        elif request.metadata.get("purpose") in ("vision", "hyde"):  # nor does a vision/HyDE aux call
            item = self._auto(request)
        else:
            item = self.script.popleft() if self.script else self._auto(request)
        if item.error:
            raise item.error
        for word in _chunks(item.text):
            yield TextDelta(word)
        for name, args in item.tool_calls:
            self._n += 1
            yield ToolCall(id=f"call_{self._n}", name=name, arguments=json.dumps(args))
        prompt = prompt_chars(request.messages) // 4
        yield Usage(prompt_tokens=prompt, completion_tokens=max(1, len(item.text) // 4), cost_usd=0.0)
        yield Finish("tool_calls" if item.tool_calls else "stop")

    def _auto(self, request: LLMRequest) -> Scripted:
        if request.metadata.get("purpose") == "hyde":
            return Scripted(text=_hyde(_last_user_text(request.messages)))
        last = request.messages[-1] if request.messages else {}
        if last.get("role") == "tool":
            content = str(last.get("content") or "")
            return Scripted(text=f"Tool result:\n{content[:2000]}")
        if last.get("role") == "user" and count_images(last.get("content")) and _is_tool_image(last):
            prev = _last_tool_text(request.messages)
            return Scripted(text=f"Tool result:\n{prev[:2000]} [+{count_images(last['content'])} images]")
        if request.metadata.get("purpose") == "vision":
            n = sum(count_images(m.get("content")) for m in request.messages)
            return Scripted(text=f"vision: {_last_user_text(request.messages)} [+{n} images]")
        user = _last_user_text(request.messages)
        images = count_images(_last_user(request.messages).get("content"))
        if images and not user.startswith("/tool "):
            return Scripted(text=f"echo: {user} [+{images} images]")
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
        return max(1, len(shrink_data_urls(text)) // 4)

    def context_window(self, model: str) -> int:
        return self.context_window_tokens

    def price(self, model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
        return 0.0

    def supports_vision(self, model: str) -> bool:
        return self.vision


def _summary(request: LLMRequest) -> Scripted:
    """Deterministic compaction summary: the template headings plus a short digest."""
    transcript = str(request.messages[1].get("content") or "") if len(request.messages) > 1 else ""
    lines = [ln for ln in transcript.splitlines() if ln.strip()]
    heads = ("Objective", "Key concepts", "Files and code", "Errors and fixes", "Pending/blocked")
    body = [f"## {h}\n- (fake summary)" for h in heads]
    body.append(f"## Current work\n- {len(lines)} transcript lines summarized")
    body.append(f'## Next step\n> "{(lines[-1] if lines else "")[:120]}"')
    body.append("## Critical context\n- none")
    return Scripted(text="\n\n".join(body))


def _hyde(query: str) -> str:
    """Deterministic hypothetical memory fact built from the query's words (HyDE aux call)."""
    words = [w for w in "".join(c if c.isalnum() else " " for c in query.lower()).split() if len(w) > 2]
    return f"{' '.join(words[:24]).capitalize() or 'Nothing'} (hypothetical)."


def _chunks(text: str, size: int = 16) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


def _last_user(messages: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    return next((m for m in reversed(messages) if m.get("role") == "user"), {})


def _is_tool_image(message: dict[str, Any]) -> bool:
    return text_of(message.get("content")).lstrip().startswith("<tool-image")


def _last_tool_text(messages: tuple[dict[str, Any], ...]) -> str:
    return next((str(m.get("content") or "") for m in reversed(messages) if m.get("role") == "tool"), "")


def _last_user_text(messages: tuple[dict[str, Any], ...]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = text_of(m.get("content"))
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
