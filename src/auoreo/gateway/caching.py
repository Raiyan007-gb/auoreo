"""Prompt-cache telemetry for one shaped request (M7 W1b, `m7.token.telemetry`). No `litellm`.

A provider cache hit needs a byte-identical prefix (tools, then system, then the messages in
order). `describe()` reduces the kwargs that are about to be sent to:

* `prefix_hashes`: a hash chain over the messages, `h_i = sha256(h_{i-1} + canonical(m_i))[:16]`,
  so two requests share a prefix exactly as far as their chains agree (`divergence`);
* `tools_hash`: one hash of the tool definitions (first in cache order: a change misses everything);
* `breakpoints`: where the `cache_control` marks sit (`"tools"` or a message index).

Hashes ignore the marks themselves (`cache_control` keys, and the string → text-block rewrite a
mark causes), because marks move from request to request without changing what the provider
caches. Inline images are hashed by a digest of their data URL. Only hashes, indexes and roles
leave this module: never message text.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

HASH_CHARS = 16
MARK = "cache_control"


@dataclass(frozen=True)
class CacheInfo:
    """What one request looked like to the provider's prompt cache."""

    prefix_hashes: tuple[str, ...]
    tools_hash: str
    breakpoints: tuple[str | int, ...] = ()
    roles: tuple[str, ...] = ()

    @property
    def prefix_hash(self) -> str:
        """Hash of the whole message list ('' for an empty request)."""
        return self.prefix_hashes[-1] if self.prefix_hashes else ""

    def event_data(self) -> dict[str, Any]:
        """Fields merged into the `usage` event."""
        return {
            "cache.prefix_hash": self.prefix_hash,
            "tools.hash": self.tools_hash,
            "cache.breakpoints": list(self.breakpoints),
        }

    def span_attrs(self) -> dict[str, Any]:
        """The same fields as `llm.call` span attributes (scalars only)."""
        return {**self.event_data(), "cache.breakpoints": ",".join(str(b) for b in self.breakpoints)}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:HASH_CHARS]


def _part(part: Any) -> Any:
    if not isinstance(part, dict):
        return part
    out = {k: v for k, v in part.items() if k != MARK}
    image = out.get("image_url")
    if out.get("type") == "image_url" and isinstance(image, dict) and "url" in image:
        out["image_url"] = {**image, "url": f"sha256:{_digest(str(image['url']))}"}
    return out


def canonical(message: dict[str, Any]) -> str:
    """Stable JSON of one message without its cache marks (a marked string equals the string)."""
    out = {k: v for k, v in message.items() if k != MARK}
    content = out.get("content")
    if isinstance(content, str) and content:
        out["content"] = [{"type": "text", "text": content}]
    elif isinstance(content, list):
        out["content"] = [_part(p) for p in content]
    return json.dumps(out, sort_keys=True, default=str)


def prefix_hashes(messages: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    """The hash chain: entry `i` identifies messages `0..i`."""
    out: list[str] = []
    prev = ""
    for m in messages:
        prev = _digest(prev + canonical(m))
        out.append(prev)
    return tuple(out)


def tools_hash(tools: Iterable[dict[str, Any]] | None) -> str:
    """One hash of the tool definitions, in the order they are sent."""
    plain = [{k: v for k, v in t.items() if k != MARK} for t in tools or ()]
    return _digest(json.dumps(plain, sort_keys=True, default=str))


def _marked(message: dict[str, Any]) -> bool:
    if MARK in message:
        return True
    content = message.get("content")
    return isinstance(content, list) and any(isinstance(p, dict) and MARK in p for p in content)


def breakpoints(kwargs: dict[str, Any]) -> tuple[str | int, ...]:
    """Positions of the cache marks: `"tools"` and/or the index of each marked message."""
    out: list[str | int] = []
    if any(isinstance(t, dict) and MARK in t for t in kwargs.get("tools") or ()):
        out.append("tools")
    out.extend(i for i, m in enumerate(kwargs.get("messages") or ()) if _marked(m))
    return tuple(out)


def divergence(previous: Sequence[str], current: Sequence[str]) -> int:
    """Index of the first message where two hash chains differ: `len(previous)` when `current`
    extends `previous` (the whole earlier request is a prefix), `len(current)` when it is shorter."""
    for i, (a, b) in enumerate(zip(previous, current, strict=False)):
        if a != b:
            return i
    return min(len(previous), len(current))


def describe(kwargs: dict[str, Any]) -> CacheInfo:
    """Telemetry for shaped LiteLLM kwargs (`messages`, optional `tools`)."""
    messages = list(kwargs.get("messages") or ())
    return CacheInfo(
        prefix_hashes=prefix_hashes(messages),
        tools_hash=tools_hash(kwargs.get("tools")),
        breakpoints=breakpoints(kwargs),
        roles=tuple(str(m.get("role", "")) for m in messages),
    )


__all__ = [
    "CacheInfo",
    "breakpoints",
    "canonical",
    "describe",
    "divergence",
    "prefix_hashes",
    "tools_hash",
]
