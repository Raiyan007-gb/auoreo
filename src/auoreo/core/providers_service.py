"""Settings → Models: connect a provider with one key, cache its model list, pick models.

- `add` validates the key by listing the provider's models; only on success is the key written
  to the secret store (the provider's LiteLLM env name for its first key, `provider/<id>/<slot>`
  for more — the `gateway.keypool` naming, so extra keys form a pool), the row created and the
  list cached. A failure returns the provider's error (redacted) and stores nothing.
- `refresh` refetches; a failure keeps the old cache and marks the status. The cache is
  refetched on read when older than `provider_store.CACHE_TTL` (1 h).
- `selection_updates` turns `{main, small, embedding}` refs into settings writes
  (`model` / `small_model` / `embed_model` [+ `embed_dims`]) that the caller applies through the
  live-settings path, so the gateway switches from the next step without a restart.

Events: `providers.changed {action, connection_id, provider, status}`, `models.changed`.
Audit: `provider.add|update|delete|refresh`, `models.select` — names / ids only, never a key.
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from auoreo.core import provider_store as store
from auoreo.core.db import utcnow
from auoreo.gateway import providers as reg
from auoreo.gateway.keypool import slot_name
from auoreo.gateway.provider_fetch import ModelInfo, ProviderFetchError, guess_kind, list_models
from auoreo.schema.providers import ProviderConnectionOut, ProviderResult, ProviderSpecOut

ROLE_FIELD: dict[str, str] = {"main": "model", "small": "small_model", "embedding": "embed_model"}
KNOWN_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "gemini-embedding-001": 3072,
    "text-embedding-004": 768,
    "mistral-embed": 1024,
    "embed-english-v3.0": 1024,
    "embed-multilingual-v3.0": 1024,
    "embed-v4.0": 1536,
    "nomic-embed-text": 768,
}
"""Embedding widths we can fill in for `embed_dims` (others keep the memory default)."""
SINGLE = frozenset({"ollama", "azure"})
"""Providers whose endpoint is process-wide (`*_API_BASE`): one connection each."""


class ProviderError(ValueError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def spec_out(spec: reg.ProviderSpec) -> ProviderSpecOut:
    return ProviderSpecOut(
        id=spec.id,
        name=spec.name,
        prefix=spec.prefix,
        needs_key=spec.needs_key,
        needs_base_url=spec.needs_base_url,
        needs_api_version=spec.needs_api_version,
        manual_models=spec.manual_models,
        default_base_url=spec.default_base_url,
        default_api_version=spec.default_api_version,
        docs_url=spec.docs_url,
    )


def conn_out(row: Any) -> ProviderConnectionOut:
    spec = reg.PROVIDERS.get(row.provider)
    return ProviderConnectionOut(
        id=row.id,
        provider=row.provider,
        provider_name=spec.name if spec else row.provider,
        label=row.label,
        secret_name=row.secret_name,
        base_url=row.base_url,
        api_version=row.api_version,
        status=row.status,
        last_error=row.last_error,
        model_count=row.model_count,
        checked_at=row.checked_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _spec(provider: str) -> reg.ProviderSpec:
    try:
        return reg.get(provider)
    except KeyError as exc:
        raise ProviderError(404, str(exc)) from None


def _override(svc: Any) -> str | None:
    """Test-only base URL (real env + fake gateway): e2e serves a local model list."""
    value = os.environ.get("AUOREO_TEST_PROVIDER_BASE")
    return value if value and svc.settings.gateway == "fake" else None


def _check_url(url: str | None) -> str | None:
    if url is None or not url.strip():
        return None
    url = url.strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+", url):
        raise ProviderError(422, "the endpoint must be an http(s) URL")
    return url


def _deployments(spec: reg.ProviderSpec, names: list[str]) -> list[ModelInfo]:
    clean = [n.strip() for n in names if n.strip()]
    return [ModelInfo(n, n, guess_kind(n)) for n in dict.fromkeys(clean)]


async def _fetch(
    svc: Any, spec: reg.ProviderSpec, key: str | None, base: str | None, ver: str | None
) -> list[ModelInfo]:
    return await list_models(spec, key, base_url=base, api_version=ver, override=_override(svc))


async def _audit(svc: Any, action: str, detail: dict[str, Any]) -> None:
    await svc.audit.record(actor="api", action=action, decision="allow", detail=detail)


async def _changed(svc: Any, action: str, row: Any) -> None:
    data = {"action": action, "connection_id": row.id, "provider": row.provider, "status": row.status}
    await svc.emit("server", "providers.changed", data)


async def _secrets_written(svc: Any, name: str | None, action: str) -> None:
    from auoreo.core.live_settings import live_of

    await live_of(svc).secrets_changed(name, action)


def _slot(spec: reg.ProviderSpec, label: str | None, taken: set[str]) -> str | None:
    if spec.secret is None:
        return None
    if spec.secret not in taken:
        return spec.secret
    base = re.sub(r"[^a-z0-9_-]+", "-", (label or "key").lower()).strip("-") or "key"
    name, n = slot_name(spec.id, base), 2
    while name in taken:
        name, n = slot_name(spec.id, f"{base}-{n}"), n + 1
    return name


async def add(
    svc: Any,
    provider: str,
    key: str | None,
    base_url: str | None,
    api_version: str | None,
    label: str | None,
    deployments: list[str],
) -> ProviderResult:
    spec = _spec(provider)
    existing = await store.by_provider(svc.db, provider)
    if provider in SINGLE and existing:
        raise ProviderError(409, f"{spec.name} is already connected: replace its key or remove it first")
    key = (key or "").strip() or None
    if spec.needs_key and not key:
        raise ProviderError(422, f"{spec.name} needs an API key")
    base = _check_url(base_url) or (spec.default_base_url if spec.needs_base_url else None)
    ver = (api_version or "").strip() or spec.default_api_version if spec.needs_api_version else None
    if spec.manual_models and not [d for d in deployments if d.strip()]:
        raise ProviderError(422, f"{spec.name}: enter at least one deployment name")
    try:
        models = await _fetch(svc, spec, key, base, ver)
    except ProviderFetchError as exc:
        return ProviderResult(ok=False, status=exc.status, error=exc.message)
    if spec.manual_models:
        models = _deployments(spec, deployments)
    taken = {c.secret_name for c in await store.connections(svc.db) if c.secret_name}
    secret = _slot(spec, label, taken)
    await asyncio.to_thread(_store_secrets, svc, spec, secret, key, base, ver)
    row = await store.create(
        svc.db,
        {
            "provider": provider,
            "label": (label or spec.name)[:128],
            "secret_name": secret,
            "base_url": base,
            "api_version": ver,
        },
        models,
    )
    await _secrets_written(svc, secret or spec.base_url_secret, "set")
    await _audit(
        svc,
        "provider.add",
        {"connection_id": row.id, "provider": provider, "secret": secret, "model_count": len(models)},
    )
    await _changed(svc, "add", row)
    return ProviderResult(ok=True, connection=conn_out(row), status="ok", model_count=len(models))


def _store_secrets(
    svc: Any, spec: reg.ProviderSpec, secret: str | None, key: str | None, base: str | None, ver: str | None
) -> None:
    if secret and key:
        svc.secrets.set(secret, key)
    if spec.base_url_secret and base:
        svc.secrets.set(spec.base_url_secret, base)
    if spec.version_secret and ver:
        svc.secrets.set(spec.version_secret, ver)


async def _get(svc: Any, conn_id: int) -> Any:
    row = await store.connection(svc.db, conn_id)
    if row is None:
        raise ProviderError(404, f"no provider connection {conn_id}")
    return row


async def _must(pending: Any) -> Any:
    row = await pending
    if row is None:
        raise ProviderError(404, "the provider connection was removed meanwhile")
    return row


def _key_of(svc: Any, row: Any) -> str | None:
    return svc.secrets._resolve(row.secret_name) if row.secret_name else None


async def update(
    svc: Any,
    conn_id: int,
    key: str | None,
    label: str | None,
    base_url: str | None,
    api_version: str | None,
    deployments: list[str] | None,
) -> ProviderResult:
    row = await _get(svc, conn_id)
    spec = _spec(row.provider)
    values: dict[str, Any] = {}
    if label is not None:
        values["label"] = label[:128]
    revalidate = any(v is not None for v in (key, base_url, api_version, deployments))
    models: list[ModelInfo] | None = None
    new_key = (key or "").strip() or None
    base = _check_url(base_url) or row.base_url
    ver = (api_version or "").strip() or row.api_version
    if revalidate:
        use_key = new_key or await asyncio.to_thread(_key_of, svc, row)
        try:
            models = await _fetch(svc, spec, use_key, base, ver)
        except ProviderFetchError as exc:
            return ProviderResult(ok=False, connection=conn_out(row), status=exc.status, error=exc.message)
        if spec.manual_models:
            names = (
                deployments
                if deployments is not None
                else [m.model_id for m in await store.models(svc.db, conn_id)]
            )
            models = _deployments(spec, names)
        await asyncio.to_thread(_store_secrets, svc, spec, row.secret_name, new_key, base, ver)
        values.update(base_url=base, api_version=ver, status="ok", last_error=None, checked_at=utcnow())
    updated = await _must(store.update(svc.db, conn_id, values, models))
    if revalidate:
        await _secrets_written(svc, row.secret_name, "set")
    await _audit(
        svc,
        "provider.update",
        {
            "connection_id": conn_id,
            "provider": row.provider,
            "fields": sorted(values) + (["key"] if new_key else []),
        },
    )
    await _changed(svc, "update", updated)
    return ProviderResult(ok=True, connection=conn_out(updated), status="ok", model_count=updated.model_count)


async def refresh(svc: Any, conn_id: int) -> ProviderResult:
    """Refetch the model list; a failure keeps the cache and records the status + error."""
    row = await _get(svc, conn_id)
    spec = _spec(row.provider)
    key = await asyncio.to_thread(_key_of, svc, row)
    try:
        models = await _fetch(svc, spec, key, row.base_url, row.api_version)
    except ProviderFetchError as exc:
        values = {"status": exc.status, "last_error": exc.message, "checked_at": utcnow()}
        updated = await _must(store.update(svc.db, conn_id, values))
        await _audit(svc, "provider.refresh", {"connection_id": conn_id, "status": exc.status})
        await _changed(svc, "refresh", updated)
        return ProviderResult(
            ok=False,
            connection=conn_out(updated),
            status=exc.status,
            error=exc.message,
            model_count=row.model_count,
        )
    if spec.manual_models:
        models = _deployments(spec, [m.model_id for m in await store.models(svc.db, conn_id)])
    values = {"status": "ok", "last_error": None, "checked_at": utcnow()}
    updated = await store.update(svc.db, conn_id, values, models)
    await _audit(
        svc, "provider.refresh", {"connection_id": conn_id, "status": "ok", "model_count": len(models)}
    )
    await _changed(svc, "refresh", updated)
    return ProviderResult(ok=True, connection=conn_out(updated), status="ok", model_count=len(models))


def backs(svc: Any, row: Any, others: list[Any]) -> list[str]:
    """Selected roles (`main`, …) served by `row`'s provider when no other connection of it remains."""
    if others:
        return []
    prefix = reg.get(row.provider).prefix
    st = svc.settings
    values = {"main": st.model, "small": st.small_model, "embedding": st.embed_model}
    return [role for role, v in values.items() if v and v.startswith(prefix)]


async def remove(svc: Any, conn_id: int, force: bool) -> list[str]:
    row = await _get(svc, conn_id)
    spec = _spec(row.provider)
    others = [c for c in await store.by_provider(svc.db, row.provider) if c.id != conn_id]
    if (roles := backs(svc, row, others)) and not force:
        raise ProviderError(
            409,
            f"{spec.name} serves the selected {', '.join(roles)} model: choose another "
            "model first, or remove with force=1",
        )
    names = [n for n in [row.secret_name] if n]
    if not others:
        names += [n for n in (spec.base_url_secret, spec.version_secret) if n]
    await asyncio.to_thread(lambda: [svc.secrets.delete(n) for n in names])
    await store.remove(svc.db, conn_id)
    await _secrets_written(svc, row.secret_name, "delete")
    await _audit(
        svc, "provider.delete", {"connection_id": conn_id, "provider": row.provider, "forced": bool(roles)}
    )
    row.status = "unchecked"
    await _changed(svc, "delete", row)
    return names


__all__ = [
    "KNOWN_DIMS",
    "ROLE_FIELD",
    "ProviderError",
    "add",
    "backs",
    "conn_out",
    "refresh",
    "remove",
    "spec_out",
    "update",
]
