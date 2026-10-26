"""OpenRouter lane (spec §25.9 L1001, §24.4 L830). No `litellm` here.

- `provider_prefs(cfg, structured)` → the `provider` request object: `sort`
  (`{by, partition: none}`), `require_parameters` (auto when tools / `response_format`),
  `data_collection: deny` + `zdr: true` from the privacy setting. `order`/`weight` are never
  sent (those are Router concepts).
- `strip_nitro(model, allow)` gates `:nitro` SKUs behind `allow_nitro`.
- `fetch_catalog()` reads `GET /api/v1/models` (cached `ttl_s`, offline-safe: a failure keeps
  the previous catalog or returns `[]`); `recency_sort()` orders ids newest-first with the
  §24.4 exclusions, which feeds `prefer_fast_model` aux ordering.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

log = logging.getLogger(__name__)
CATALOG_URL = "https://openrouter.ai/api/v1/models"
EXCLUDE = re.compile(r"thinking|reason|:free|:batch|embed|audio|vl\b|-vl|tts", re.I)
_PRIVACY: dict[str, dict[str, Any]] = {
    "default": {},
    "deny_collection": {"data_collection": "deny"},
    "zdr": {"data_collection": "deny", "zdr": True},
}
_catalog: dict[str, Any] = {"at": 0.0, "models": []}


def is_openrouter(model: str) -> bool:
    return model.startswith("openrouter/")


def provider_prefs(cfg: Any, *, structured: bool) -> dict[str, Any]:
    """`cfg` is `core.settings_i2.OpenRouterSettings`; `structured` = tools or response_format."""
    out: dict[str, Any] = {}
    if cfg.sort:
        out["sort"] = {"by": cfg.sort, "partition": "none"}
    require = {"always": True, "never": False, "auto": structured}[cfg.require_parameters]
    if require:
        out["require_parameters"] = True
    out.update(_PRIVACY[cfg.privacy])
    return out


def strip_nitro(model: str, allow: bool) -> str:
    if allow or not model.endswith(":nitro"):
        return model
    log.info("%s: :nitro is gated by i2.openrouter.allow_nitro; using the base SKU", model)
    return model.removesuffix(":nitro")


def cached_catalog(ttl_s: float = 3600.0) -> list[dict[str, Any]]:
    """The last fetched catalog while fresh (never fetches)."""
    if time.monotonic() - float(_catalog["at"]) > ttl_s:
        return []
    return list(_catalog["models"])


def set_catalog(models: list[dict[str, Any]]) -> None:
    _catalog.update(at=time.monotonic(), models=list(models))


def clear_catalog() -> None:
    _catalog.update(at=0.0, models=[])


async def fetch_catalog(
    ttl_s: float = 3600.0, *, client: Any = None, timeout_s: float = 5.0
) -> list[dict[str, Any]]:
    """Cached for `ttl_s`; offline / non-200 → the stale catalog (or `[]`), never raises."""
    if fresh := cached_catalog(ttl_s):
        return fresh
    import httpx

    owns = client is None
    client = client or httpx.AsyncClient(timeout=timeout_s)
    try:
        resp = await client.get(CATALOG_URL)
        if resp.status_code != 200:
            raise ValueError(f"HTTP {resp.status_code}")
        models = [m for m in (resp.json().get("data") or []) if isinstance(m, dict) and m.get("id")]
    except Exception as exc:  # offline-safe
        log.info(
            "OpenRouter catalog unavailable (%s); keeping %d cached models", exc, len(_catalog["models"])
        )
        return list(_catalog["models"])
    finally:
        if owns:
            await client.aclose()
    set_catalog(models)
    return models


def recency_sort(models: list[dict[str, Any]]) -> list[str]:
    """Catalog ids newest-first (`created` desc), minus thinking/free/batch/embed/audio/vl/tts."""
    keep = [m for m in models if not EXCLUDE.search(str(m.get("id", "")))]
    keep.sort(key=lambda m: (-int(m.get("created") or 0), str(m.get("id"))))
    return [str(m["id"]) for m in keep]


def prefer_fast(entries: list[str], ttl_s: float = 3600.0) -> list[str]:
    """Reorder OpenRouter chain entries newest-first by the cached catalog; entries the
    catalog does not know (or non-OpenRouter ones) keep their relative order after them."""
    ranked = {mid: i for i, mid in enumerate(recency_sort(cached_catalog(ttl_s)))}
    if not ranked:
        return list(entries)

    def rank(e: str) -> tuple[int, int]:
        mid = e.removeprefix("openrouter/")
        return (ranked[mid], 0) if mid in ranked else (len(ranked), entries.index(e))

    return sorted(entries, key=rank)


__all__ = [
    "CATALOG_URL",
    "cached_catalog",
    "clear_catalog",
    "fetch_catalog",
    "is_openrouter",
    "prefer_fast",
    "provider_prefs",
    "recency_sort",
    "set_catalog",
    "strip_nitro",
]
