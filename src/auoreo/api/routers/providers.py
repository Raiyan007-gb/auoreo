"""`/api/providers` + `/api/models/selection` — Settings → Models.

- `POST /api/providers/detect {key}` — which provider a key looks like (nothing is stored).
- `POST /api/providers` — validate a key by listing the provider's models; on success store the
  key (secret store), the connection row and the model cache; on failure return the provider's
  error (redacted) and store nothing.
- `GET /api/providers`, `GET /api/providers/{id}/models?kind=&q=&refresh=`,
  `POST /api/providers/{id}/refresh`, `PUT /api/providers/{id}`, `DELETE /api/providers/{id}`.
- `GET|PUT /api/models/selection` — main / small / embedding model, written to the settings store
  and applied live (the gateway switches from the agent's next step, no restart).

Same guard as `/api/env`: normal auth + cookie CSRF, capability tokens refused, bodies ≤ 64 KiB.
No response, event or audit row ever contains a key.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from auoreo.api.deps import ServicesDep
from auoreo.api.routers.env_api import ENV_AUTH, note_for, write_settings
from auoreo.core import env_store, model_selection
from auoreo.core import provider_store as store
from auoreo.core import providers_service as svc_mod
from auoreo.core.db import utcnow
from auoreo.core.live_settings import live_of
from auoreo.core.providers_service import ProviderError, conn_out, spec_out
from auoreo.core.services import Services
from auoreo.gateway import providers as reg
from auoreo.schema.providers import (
    ModelSelection,
    ModelSelectionApplied,
    ModelSelectionWrite,
    ProviderAdopt,
    ProviderCreate,
    ProviderDetectRequest,
    ProviderDetectResult,
    ProviderList,
    ProviderModelOut,
    ProviderModels,
    ProviderResult,
    ProviderUpdate,
)

router = APIRouter(tags=["providers"], dependencies=ENV_AUTH)


def _http(exc: ProviderError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.post("/api/providers/detect", response_model=ProviderDetectResult)
async def detect(body: ProviderDetectRequest) -> ProviderDetectResult:
    """Guess the provider from the key's shape (never stored, never sent anywhere)."""
    found = reg.detect_provider(body.key)
    ids = [str(c) for c in found["candidates"]]  # type: ignore[attr-defined]
    return ProviderDetectResult(
        candidates=ids, confident=bool(found["confident"]), providers=[spec_out(reg.get(i)) for i in ids]
    )


@router.get("/api/providers", response_model=ProviderList)
async def list_providers(svc: ServicesDep) -> ProviderList:
    rows = await store.connections(svc.db)
    return ProviderList(
        connections=[conn_out(r) for r in rows], providers=[spec_out(s) for s in reg.PROVIDERS.values()]
    )


@router.post("/api/providers", response_model=ProviderResult)
async def add_provider(body: ProviderCreate, svc: ServicesDep) -> ProviderResult:
    try:
        return await svc_mod.add(
            svc, body.provider, body.key, body.base_url, body.api_version, body.label, body.deployments
        )
    except ProviderError as exc:
        raise _http(exc) from exc


@router.post("/api/providers/adopt", response_model=ProviderResult)
async def adopt_provider(body: ProviderAdopt, svc: ServicesDep) -> ProviderResult:
    """Manage a provider here when its key already lives in Settings → Environment or the real
    environment: list its models and switch between them, without pasting the key again."""
    try:
        return await svc_mod.adopt(svc, body.provider)
    except ProviderError as exc:
        raise _http(exc) from exc


@router.put("/api/providers/{conn_id}", response_model=ProviderResult)
async def update_provider(conn_id: int, body: ProviderUpdate, svc: ServicesDep) -> ProviderResult:
    """Replace the key / label / endpoint (a new key or endpoint is validated first)."""
    try:
        return await svc_mod.update(
            svc, conn_id, body.key, body.label, body.base_url, body.api_version, body.deployments
        )
    except ProviderError as exc:
        raise _http(exc) from exc


@router.post("/api/providers/{conn_id}/refresh", response_model=ProviderResult)
async def refresh_provider(conn_id: int, svc: ServicesDep) -> ProviderResult:
    try:
        return await svc_mod.refresh(svc, conn_id)
    except ProviderError as exc:
        raise _http(exc) from exc


@router.delete("/api/providers/{conn_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    conn_id: int, svc: ServicesDep, force: Annotated[int, Query(ge=0, le=1)] = 0
) -> None:
    """Remove the connection and its key; refused (409) while it serves the selected model."""
    try:
        await svc_mod.remove(svc, conn_id, bool(force))
    except ProviderError as exc:
        raise _http(exc) from exc


@router.get("/api/providers/{conn_id}/models", response_model=ProviderModels)
async def provider_models(
    conn_id: int,
    svc: ServicesDep,
    kind: Literal["chat", "embedding", "other"] | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    refresh: Annotated[int, Query(ge=0, le=1)] = 0,
) -> ProviderModels:
    """The cached list (refetched when `refresh=1` or older than 1 h; a failed fetch keeps it)."""
    conn = await store.connection(svc.db, conn_id)
    if conn is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no provider connection {conn_id}")
    rows = await store.models(svc.db, conn_id)
    fetched = store.fetched_at(rows, conn)
    stale = fetched is None or (utcnow() - fetched) > store.CACHE_TTL
    if refresh or stale:
        await svc_mod.refresh(svc, conn_id)
        conn = await store.connection(svc.db, conn_id) or conn
    rows = await store.models(svc.db, conn_id, kind, q)
    fetched = store.fetched_at(rows, conn) if rows else conn.checked_at
    prefix = reg.PROVIDERS[conn.provider].prefix if conn.provider in reg.PROVIDERS else ""
    return ProviderModels(
        connection_id=conn_id,
        status=conn.status,
        fetched_at=fetched,
        stale=fetched is None or (utcnow() - fetched) > store.CACHE_TTL,
        error=conn.last_error,
        models=[
            ProviderModelOut(
                model_id=r.model_id,
                litellm_id=prefix + r.model_id,
                name=r.name,
                kind=r.kind,
                context_window=r.context_window,
                input_price=r.input_price,
                output_price=r.output_price,
                capabilities=[str(c) for c in (r.meta or {}).get("capabilities") or []],
            )
            for r in rows
        ],
    )


async def _selection(svc: Services) -> ModelSelection:
    data = env_store.read(svc.settings.home)
    return await model_selection.current(svc, data, live_of(svc).overrides)


@router.get("/api/models/selection", response_model=ModelSelection)
async def get_selection(svc: ServicesDep) -> ModelSelection:
    return await _selection(svc)


@router.put("/api/models/selection", response_model=ModelSelectionApplied)
async def put_selection(body: ModelSelectionWrite, svc: ServicesDep) -> ModelSelectionApplied:
    """Choose models from connected providers; applied live through the settings store."""
    data = env_store.read(svc.settings.home)
    try:
        new, values = await model_selection.updates(svc, body, data, live_of(svc).overrides)
    except ProviderError as exc:
        raise _http(exc) from exc
    if not values:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "choose at least one of main, small, embedding"
        )
    res = await write_settings(svc, new, "models")
    await svc.audit.record(actor="api", action="models.select", decision="allow", detail={"values": values})
    await svc.emit("server", "models.changed", {"roles": sorted(values), "values": values})
    return ModelSelectionApplied(
        selection=await _selection(svc),
        changed=res.changed,
        live=res.live,
        restart_required=res.restart_required,
        note=note_for(res),
    )


__all__ = ["router"]
