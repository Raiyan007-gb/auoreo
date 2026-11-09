"""Structured citations for connector replies (M7 W3, `m7.agents.citations`).

After a routed turn, `InboundRouter` reads that run's `tool.completed` events and puts
`citations: [{tool_call_id, url, title}]` in the reply meta; each plugin renders them in its own
format. The runner, `TurnResult` and the `run.completed` event are untouched, so there is no
wire change.

What counts:
- a non-error `web_fetch` result, by `meta.url` (the final URL after redirects); denied,
  robots-blocked and 4xx fetches are errors and are skipped;
- a `web_search` hit (`meta.sources`) only when its URL appears in the answer text; a fetched
  URL takes its title from a matching hit.
URLs are compared without their fragment, kept in first-seen order and capped at `CAP`. The
`tool_call_id` is the fetch that produced the page, else the search that listed it. Events of
delegated sub-sessions are other aggregates and are not read. Titles and URLs are untrusted:
control and format characters never leave this module, and plugins render plain text.

Design: docs/designs/m7-agents-bots.md.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urldefrag, urlsplit

from auoreo.core.event_store import REPLAY_LIMIT

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

CAP = 8
TITLE_CAP = 120
URL_CAP = 2000
MAX_PAGES = 20
"""Event pages read per reply (`REPLAY_LIMIT` each): a bound, not an expectation."""
ANSWERED = frozenset({"completed", "max_steps"})


@dataclass
class _Entry:
    url: str
    title: str | None = None
    fetch: str | None = None
    search: str | None = None


def clean_title(raw: Any) -> str | None:
    """One line of plain text, at most `TITLE_CAP` characters; None when nothing is left."""
    if not isinstance(raw, str):
        return None
    spaced = "".join(" " if ch.isspace() else ch for ch in raw)
    kept = "".join(ch for ch in spaced if not unicodedata.category(ch).startswith("C"))
    return " ".join(kept.split())[:TITLE_CAP].rstrip() or None


def clean_url(raw: Any) -> str | None:
    """An http(s) URL without its fragment; None for anything else (never repaired)."""
    if not isinstance(raw, str) or not 0 < len(raw) <= URL_CAP:
        return None
    if any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in raw):
        return None
    url = urldefrag(raw).url
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return url if parts.scheme in {"http", "https"} and parts.netloc else None


def _hits(meta: dict[str, Any]) -> Iterable[tuple[str, str | None]]:
    sources = meta.get("sources")
    for hit in sources if isinstance(sources, list) else ():
        url = clean_url(hit.get("url")) if isinstance(hit, dict) else None
        if url is not None:
            yield url, clean_title(hit.get("title"))


def from_events(events: Iterable[Any], reply_text: str) -> list[dict[str, Any]]:
    """Citations from `tool.completed` payloads (`{call_id, tool, is_error, meta}`), in order."""
    seen: dict[str, _Entry] = {}
    for ev in events:
        if not isinstance(ev, dict) or ev.get("is_error") or not isinstance(ev.get("meta"), dict):
            continue
        call, tool, meta = ev.get("call_id"), ev.get("tool"), ev["meta"]
        if not isinstance(call, str) or not call:
            continue
        if tool == "web_search":
            for url, title in _hits(meta):
                entry = seen.setdefault(url, _Entry(url, search=call))
                entry.title = entry.title or title
                entry.search = entry.search or call
        elif tool == "web_fetch" and (page := clean_url(meta.get("url"))) is not None:
            entry = seen.setdefault(page, _Entry(page))
            entry.fetch = entry.fetch or call
    used = [e for e in seen.values() if e.fetch or e.url in reply_text]
    return [{"tool_call_id": e.fetch or e.search, "url": e.url, "title": e.title} for e in used[:CAP]]


async def for_run(
    services: Services, session_id: str, run_id: str | None, after_seq: int | None, result: Any
) -> list[dict[str, Any]]:
    """The citations of one finished run; [] when the flag was off at submit (`after_seq` is
    None), the run gave no answer, or the events cannot be read (best effort, never raises)."""
    if after_seq is None or run_id is None or result is None or result.status not in ANSWERED:
        return []
    try:
        found: list[dict[str, Any]] = []
        cursor, channels = after_seq, frozenset({f"session:{session_id}"})
        for _ in range(MAX_PAGES):
            page = await services.events.replay(cursor, channels)
            found += [e.data for e in page if e.type == "tool.completed" and e.data.get("run_id") == run_id]
            if len(page) < REPLAY_LIMIT:
                break
            cursor = page[-1].seq
        return from_events(found, result.text or "")
    except Exception:
        log.exception("could not read citations for run %s", run_id)
        return []


async def mark(services: Services) -> int | None:
    """The event cursor to read a run from, taken just before `submit`; None = flag off."""
    if not services.settings.m7.agents.citations:
        return None
    try:
        return await services.events.last_seq()
    except Exception:
        log.exception("could not read the event cursor; this reply carries no citations")
        return None


__all__ = ["CAP", "TITLE_CAP", "clean_title", "clean_url", "for_run", "from_events", "mark"]
