"""Search result cache, result dedup and the web_fetch denial / robots caches (spec §25.15 L1097).

One SQLite file, `<home>/search_cache.db` (sync sqlite3 off the event loop):

- `search(query_hash, kind, provider, response, created_at, ttl)` — `web_search` answers keyed by
  `sha256(normalised query | n | topic)`; TTL 24 h for search, 1 h for news (settings);
- `denials(host, status, until)` — a host that answered 401/403/429 is skipped by `web_fetch`
  until `until` (persistent across restarts, TTL `i9.fetch.denial_ttl_s`);
- `robots(host, body, fetched)` — robots.txt bodies, refreshed after a day.

Dedup: canonical URL (lower-case host without `www.`, no fragment, no `utm_*`/`fbclid`/`gclid`
query keys, no trailing slash) plus a 64-bit title simhash (Hamming distance ≤ 3 = same title).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = re.compile(r"^(utm_\w+|fbclid|gclid|mc_eid|ref_src)$", re.I)
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS search (query_hash TEXT, kind TEXT, provider TEXT, response TEXT,"
    " created_at REAL, ttl REAL, PRIMARY KEY (query_hash, kind))",
    "CREATE TABLE IF NOT EXISTS denials (host TEXT PRIMARY KEY, status INTEGER, until REAL)",
    "CREATE TABLE IF NOT EXISTS robots (host TEXT PRIMARY KEY, body TEXT, fetched REAL)",
)


def canonical_url(url: str) -> str:
    p = urlsplit(url.strip())
    host = (p.hostname or "").lower().removeprefix("www.")
    port = f":{p.port}" if p.port and p.port not in (80, 443) else ""
    query = urlencode(
        [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not _TRACKING.match(k)]
    )
    path = p.path.rstrip("/") or ""
    return urlunsplit((p.scheme.lower() or "https", host + port, path, query, ""))


def simhash(text: str) -> int:
    weights = [0] * 64
    for token in re.findall(r"\w+", text.lower()):
        h = int.from_bytes(hashlib.blake2b(token.encode(), digest_size=8).digest(), "big")
        for i in range(64):
            weights[i] += 1 if h >> i & 1 else -1
    return sum(1 << i for i, w in enumerate(weights) if w > 0)


def dedup(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop rows whose canonical URL or (near-)identical title was already seen."""
    seen_urls: set[str] = set()
    seen_titles: list[int] = []
    out = []
    for row in rows:
        url = canonical_url(str(row.get("url", "")))
        title = str(row.get("title", "")).strip()
        sig = simhash(title) if len(title.split()) >= 3 else None
        if url in seen_urls or (sig is not None and any(bin(sig ^ t).count("1") <= 3 for t in seen_titles)):
            continue
        seen_urls.add(url)
        if sig is not None:
            seen_titles.append(sig)
        out.append(row)
    return out


def query_key(query: str, n: int, topic: str = "general") -> str:
    norm = " ".join(query.lower().split())
    return hashlib.sha256(f"{norm}\0{n}\0{topic}".encode()).hexdigest()


class SearchCache:
    def __init__(self, home: Path) -> None:
        self.path = home / "search_cache.db"

    def _conn(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        for stmt in SCHEMA:
            conn.execute(stmt)
        return conn

    def _run(self, sql: str, args: tuple[Any, ...] = (), *, fetch: bool = False) -> list[tuple[Any, ...]]:
        conn = self._conn()
        try:
            cur = conn.execute(sql, args)
            rows = cur.fetchall() if fetch else []
            conn.commit()
            return rows
        finally:
            conn.close()

    async def _q(self, sql: str, args: tuple[Any, ...] = (), *, fetch: bool = False) -> list[tuple[Any, ...]]:
        return await asyncio.to_thread(self._run, sql, args, fetch=fetch)

    # ------------------------------------------------------------------ search
    async def get(self, key: str, kind: str = "search") -> dict[str, Any] | None:
        rows = await self._q(
            "SELECT provider, response, created_at, ttl FROM search WHERE query_hash=? AND kind=?",
            (key, kind),
            fetch=True,
        )
        if not rows:
            return None
        provider, response, created, ttl = rows[0]
        if created + ttl < time.time():
            await self._q("DELETE FROM search WHERE query_hash=? AND kind=?", (key, kind))
            return None
        return {"provider": provider, **json.loads(response), "cached_at": created}

    async def put(
        self, key: str, provider: str, payload: dict[str, Any], ttl: float, kind: str = "search"
    ) -> None:
        if ttl <= 0:
            return
        await self._q(
            "INSERT OR REPLACE INTO search VALUES (?, ?, ?, ?, ?, ?)",
            (key, kind, provider, json.dumps(payload), time.time(), ttl),
        )

    # ------------------------------------------------------------------ denials
    async def denied(self, host: str) -> int | None:
        rows = await self._q("SELECT status, until FROM denials WHERE host=?", (host,), fetch=True)
        if rows and rows[0][1] > time.time():
            return int(rows[0][0])
        return None

    async def deny(self, host: str, status: int, ttl: float) -> None:
        if ttl > 0:
            await self._q(
                "INSERT OR REPLACE INTO denials VALUES (?, ?, ?)", (host, status, time.time() + ttl)
            )

    # ------------------------------------------------------------------ robots
    async def robots(self, host: str, max_age: float = 86_400) -> str | None:
        rows = await self._q("SELECT body, fetched FROM robots WHERE host=?", (host,), fetch=True)
        if rows and rows[0][1] + max_age > time.time():
            return str(rows[0][0])
        return None

    async def put_robots(self, host: str, body: str) -> None:
        await self._q("INSERT OR REPLACE INTO robots VALUES (?, ?, ?)", (host, body[:200_000], time.time()))
