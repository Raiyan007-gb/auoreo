"""`<memory-context>` scrubbing (spec §11.7, §24.3 L817).

Recalled memory rides in the turn's context as a `<memory-context>` block. It must never
leak back out (the model echoing it) or in (a user pasting an old transcript):

- `sanitize_context(text)` — 3 regexes: complete blocks, an unterminated opening block (to
  the end), and stray open/close tags.
- `StreamingContextScrubber` — the same for a token stream: boundary-aware across deltas
  (a tag split over two chunks is still caught), holds back a possible partial tag, and
  `flush()` drops an unterminated span. One instance per response (`reset()` between).
"""

from __future__ import annotations

import re

TAG = "memory-context"
OPEN = f"<{TAG}>"
CLOSE = f"</{TAG}>"
_BLOCK = re.compile(rf"<{TAG}>.*?</{TAG}>\n?", re.S | re.I)
_UNTERMINATED = re.compile(rf"<{TAG}>.*\Z", re.S | re.I)
_STRAY = re.compile(rf"</?{TAG}>", re.I)


def sanitize_context(text: str) -> str:
    if TAG not in text.lower():
        return text
    text = _BLOCK.sub("", text)
    text = _UNTERMINATED.sub("", text)
    return _STRAY.sub("", text)


def _partial_suffix(text: str, tag: str) -> int:
    """Length of the longest suffix of `text` that is a proper prefix of `tag`."""
    low = text.lower()
    for n in range(min(len(tag) - 1, len(low)), 0, -1):
        if tag.startswith(low[-n:]):
            return n
    return 0


class StreamingContextScrubber:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._buf = ""
        self._inside = False

    def feed(self, chunk: str) -> str:
        """Return the text of `chunk` that is safe to emit now."""
        self._buf += chunk
        out: list[str] = []
        while self._buf:
            low = self._buf.lower()
            if self._inside:
                at = low.find(CLOSE)
                if at < 0:
                    keep = _partial_suffix(self._buf, CLOSE)
                    self._buf = self._buf[len(self._buf) - keep :] if keep else ""
                    break
                self._buf = self._buf[at + len(CLOSE) :]
                self._inside = False
                continue
            at = low.find(OPEN)
            stray = low.find(CLOSE)
            if stray >= 0 and (at < 0 or stray < at):  # a close tag with no open: drop the tag
                out.append(self._buf[:stray])
                self._buf = self._buf[stray + len(CLOSE) :]
                continue
            if at < 0:
                keep = _partial_suffix(self._buf, OPEN)
                keep = max(keep, _partial_suffix(self._buf, CLOSE))
                cut = len(self._buf) - keep
                out.append(self._buf[:cut])
                self._buf = self._buf[cut:]
                break
            out.append(self._buf[:at])
            self._buf = self._buf[at + len(OPEN) :]
            self._inside = True
        return "".join(out)

    def flush(self) -> str:
        """End of stream: an unterminated block is dropped; a held partial tag is released."""
        rest = "" if self._inside else self._buf
        self.reset()
        return rest


__all__ = ["StreamingContextScrubber", "sanitize_context"]
