"""Plugin taps (registry indexes), modelled on `skillhub.fetch.http_index` (spec §25.15 L1076).

A tap is an HTTP(S) JSON index:

    {"plugins": [{"name": "weather", "package": "auoreo-plugin-weather", "version": "1.2.0",
                  "sha256": "<wheel sha256>", "description": "…",
                  "requirements": ["dep==1.0 --hash=sha256:…"],
                  "signed_by": "<key id>", "sig": "<base64 ed25519>"}]}

`sig` (optional) signs `{name: package, version, sha256}` with a key from
`plugins.signing.PLUGIN_PUBKEYS` / `i6.plugins.extra_pubkeys`. Indexes are cached under
`<home>/cache/plugin-taps/` for `i6.plugins.index_ttl_s`; when a tap is unreachable the stale
cache is used (offline-safe) and the error is reported alongside.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from auoreo.core.settings import Settings

TIMEOUT_S = 30.0
PACKAGE_RE = re.compile(r"^(?:auoreo|oreo)-plugin-[a-z0-9][a-z0-9-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class RegistryError(RuntimeError):
    """A tap/index problem (user-facing message, never credentials)."""


@dataclass(frozen=True)
class TapEntry:
    name: str
    package: str
    version: str
    sha256: str
    tap: str
    description: str = ""
    requirements: tuple[str, ...] = ()
    url: str | None = None
    signature: dict[str, Any] = field(default_factory=dict, compare=False)


def _cache_file(home: Path, tap: str) -> Path:
    return home / "cache" / "plugin-taps" / f"{hashlib.sha256(tap.encode()).hexdigest()[:16]}.json"


async def _fetch(tap: str) -> Any:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
            r = await client.get(tap)
    except httpx.HTTPError as exc:
        raise RegistryError(f"tap {tap} unreachable ({type(exc).__name__})") from None
    if r.status_code >= 400:
        raise RegistryError(f"tap {tap} answered HTTP {r.status_code}")
    try:
        return r.json()
    except ValueError:
        raise RegistryError(f"tap {tap} is not a JSON index") from None


def parse_index(tap: str, body: Any) -> list[TapEntry]:
    rows = body.get("plugins") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise RegistryError(f"tap {tap}: index has no 'plugins' list")
    out = []
    for e in rows:
        if not isinstance(e, dict):
            continue
        package, sha = str(e.get("package", "")), str(e.get("sha256", "")).lower()
        if not PACKAGE_RE.fullmatch(package) or not SHA_RE.fullmatch(sha) or not e.get("version"):
            continue  # unpinned or not an oreo/auoreo plugin distribution: never installable
        out.append(
            TapEntry(
                name=str(e.get("name") or package.split("-plugin-", 1)[1]),
                package=package,
                version=str(e["version"]),
                sha256=sha,
                tap=tap,
                description=str(e.get("description", "")),
                requirements=tuple(str(r) for r in e.get("requirements") or []),
                url=str(e["url"]) if e.get("url") else None,
                signature={k: e[k] for k in ("sig", "signed_by") if k in e},
            )
        )
    return out


async def tap_index(settings: Settings, tap: str) -> tuple[list[TapEntry], str | None]:
    """(entries, error): fresh within the TTL, else refetched; a failed fetch serves the cache."""
    cached = _cache_file(settings.home, tap)
    ttl = settings.i6.plugins.index_ttl_s
    body: Any = None
    error: str | None = None
    if cached.is_file() and time.time() - cached.stat().st_mtime < ttl:
        body = json.loads(cached.read_text(encoding="utf-8"))
    else:
        try:
            body = await _fetch(tap)
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(body), encoding="utf-8")
        except RegistryError as exc:
            error = str(exc)
            if not cached.is_file():
                return [], error
            body = json.loads(cached.read_text(encoding="utf-8"))
            error += " (using the cached index)"
    try:
        return parse_index(tap, body), error
    except RegistryError as exc:
        return [], str(exc)


async def index(settings: Settings) -> tuple[list[TapEntry], list[str]]:
    """Every configured tap (first tap wins on a package clash) and per-tap errors."""
    out: dict[str, TapEntry] = {}
    errors: list[str] = []
    for tap in settings.i6.plugins.taps:
        entries, err = await tap_index(settings, tap)
        if err:
            errors.append(err)
        for e in entries:
            out.setdefault(e.package, e)
    return list(out.values()), errors


async def search(settings: Settings, query: str = "") -> tuple[list[TapEntry], list[str]]:
    entries, errors = await index(settings)
    q = query.lower().strip()
    hits = [e for e in entries if not q or q in e.package or q in e.name or q in e.description.lower()]
    return sorted(hits, key=lambda e: e.package), errors


__all__ = ["PACKAGE_RE", "RegistryError", "TapEntry", "index", "parse_index", "search", "tap_index"]
