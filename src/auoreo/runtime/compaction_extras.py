"""S5 summary template extras (spec §11.9), assembled around the summarizer's text.

The summarizer writes an `<analysis>` scratchpad first (stripped here) and the headed
checkpoint. Deterministic sections are then added from the compacted span itself, so the
facts the agent needs most survive even a weak summary:

- Hermes-style handoff prefix + reverse-signal clause ("newer messages win");
- `## User messages (verbatim, newest first)` — ≤ 24K chars total, ≤ 4K each;
- `## Session log` — one line per tool call, newest kept, ≤ 4K;
- `## Anchor index` — `#idx role: preview` of every archived message, ≤ 7K;
- footer pointing at `session_search(session_id=…)` for the archived transcript;
- end marker.

`prior_checkpoint` extracts an earlier checkpoint from the span so the summarizer can merge
it (the conversation wins on conflict).
"""

from __future__ import annotations

import json
import re
from typing import Any

from auoreo.runtime.compaction_plan import SUMMARY_TAG, is_real_user, tool_calls_in

HANDOFF = (
    "This session continues from an earlier part of the conversation that was compacted to "
    "fit the context window. The checkpoint below replaces the compacted messages."
)
REVERSE_SIGNAL = "If anything in the messages after this checkpoint contradicts it, the newer messages win."
END_MARKER = "[end of compaction checkpoint]"
USER_TOTAL_CHARS = 24_000
USER_EACH_CHARS = 4_000
SESSION_LOG_CHARS = 4_000
ANCHOR_CHARS = 7_000
_ANALYSIS = re.compile(r"<analysis>.*?(</analysis>|$)", re.S | re.I)
_CHECKPOINT = re.compile(r"^<compaction-summary[^>]*>\n?(.*?)(?:\n</compaction-summary>|$)", re.S)


def strip_analysis(text: str) -> str:
    """Drop the `<analysis>` scratchpad (an unterminated one runs to the first heading)."""
    out = _ANALYSIS.sub("", text) if "</analysis>" in text.lower() else text
    if out.lstrip().lower().startswith("<analysis>"):  # unterminated: keep from first heading
        at = out.find("\n## ")
        out = out[at + 1 :] if at >= 0 else ""
    return out.strip()


def prior_checkpoint(middle: list[dict[str, Any]]) -> str | None:
    """The newest earlier checkpoint inside the span being compacted (None when absent)."""
    for m in reversed(middle):
        content = m.get("content")
        if m.get("role") == "user" and isinstance(content, str) and content.startswith(SUMMARY_TAG):
            match = _CHECKPOINT.match(content)
            body = match.group(1) if match else content
            return body.split("</compaction-summary>", 1)[0].strip()[:20_000] or None
    return None


def _text(m: dict[str, Any]) -> str:
    content = m.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(p.get("text", "[image]")) for p in content if isinstance(p, dict))
    return ""


def user_messages(middle: list[dict[str, Any]]) -> str:
    out: list[str] = []
    used = 0
    for m in reversed(middle):
        text = _text(m).strip()
        if not is_real_user(m) or not text or text.startswith(("<skill ", "<skill>")):
            continue
        if len(text) > USER_EACH_CHARS:
            text = text[:USER_EACH_CHARS] + " … [clipped]"
        block = "> " + text.replace("\n", "\n> ")
        if used + len(block) > USER_TOTAL_CHARS:
            break
        used += len(block) + 1
        out.append(block)
    return "\n\n".join(out)


def _arg_hint(args: dict[str, Any]) -> str:
    for key in ("path", "command", "query", "url", "name", "pattern"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value[:80]
    return json.dumps(args, ensure_ascii=False)[:60] if args else ""


def session_log(middle: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for name, args, result in tool_calls_in(middle):
        status = "no result" if result is None else ("error" if result.lower().startswith("error") else "ok")
        size = f", {len(result)} chars" if result else ""
        lines.append(f"- {name}({_arg_hint(args)}) → {status}{size}")
    kept: list[str] = []
    used = 0
    for line in reversed(lines):  # newest kept
        if used + len(line) + 1 > SESSION_LOG_CHARS:
            kept.append(f"- … {len(lines) - len(kept)} older calls omitted")
            break
        used += len(line) + 1
        kept.append(line)
    return "\n".join(reversed(kept))


def anchor_index(middle: list[dict[str, Any]], base: int) -> str:
    lines: list[str] = []
    used = 0
    for i, m in enumerate(middle):
        preview = " ".join(_text(m).split())[:90]
        if m.get("tool_calls"):
            names = ",".join((tc.get("function") or {}).get("name", "?") for tc in m["tool_calls"])
            preview = f"{preview} [calls {names}]".strip()
        line = f"- #{base + i} {m.get('role')}: {preview}"
        if used + len(line) + 1 > ANCHOR_CHARS:
            lines.append(f"- … {len(middle) - i} more archived messages")
            break
        used += len(line) + 1
        lines.append(line)
    return "\n".join(lines)


def footer(session_id: str) -> str:
    return (
        "The full transcript of the compacted span is archived (soft-archive) and searchable: "
        f'session_search(query="…", session_id="{session_id}").'
    )


def assemble(summary: str, middle: list[dict[str, Any]], session_id: str, base: int, *, full: bool) -> str:
    """The checkpoint body. `full=False` keeps only prefix/summary/footer (shrink fallback)."""
    parts = [HANDOFF, REVERSE_SIGNAL, strip_analysis(summary)]
    if full:
        sections = (
            ("## User messages (verbatim, newest first)", user_messages(middle)),
            ("## Session log", session_log(middle)),
            ("## Anchor index", anchor_index(middle, base)),
        )
        parts += [f"{head}\n{body}" for head, body in sections if body]
    parts += [footer(session_id), END_MARKER]
    return "\n\n".join(p for p in parts if p)


__all__ = [
    "END_MARKER",
    "HANDOFF",
    "assemble",
    "prior_checkpoint",
    "strip_analysis",
]
