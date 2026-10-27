"""Model selection (main / small / embedding) on top of provider connections.

`updates(svc, write)` maps `{role: (connection_id, model_id)}` to settings keys
(`model`, `small_model`, `embed_model` [+ `embed_dims` when the width is known]) written as
`<litellm prefix><model id>`. When the store pins explicit routing groups (`models`), their
`default` / `small` deployments follow too, so the choice is what the gateway routes to.
`current(svc)` resolves the effective values back to a connection for display.
"""

from __future__ import annotations

from typing import Any

from auoreo.core import env_store
from auoreo.core import provider_store as store
from auoreo.core.providers_service import KNOWN_DIMS, ROLE_FIELD, ProviderError
from auoreo.gateway import providers as reg
from auoreo.schema.providers import ModelSelection, ModelSelectionWrite, SelectedModel

GROUP_OF = {"main": "default", "small": "small"}


async def updates(
    svc: Any, body: ModelSelectionWrite, data: dict[str, Any], overrides: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """`(new store content, {role: value})`; raises ProviderError (404/409/422)."""
    out = dict(data)
    values: dict[str, str] = {}
    for role, ref in (("main", body.main), ("small", body.small), ("embedding", body.embedding)):
        if ref is None:
            continue
        field = ROLE_FIELD[role]
        env = f"AUOREO_{field.upper()}"
        if env_store.setting_source(env, field, data, overrides) in {"env", "override"}:
            raise ProviderError(409, f"{env} is set in the real environment (or a startup flag), which wins")
        conn = await store.connection(svc.db, ref.connection_id)
        if conn is None:
            raise ProviderError(404, f"no provider connection {ref.connection_id}")
        row = await store.model(svc.db, conn.id, ref.model_id)
        if row is None:
            raise ProviderError(422, f"{ref.model_id} is not in {conn.provider}'s model list (refresh it?)")
        if role == "embedding" and row.kind != "embedding":
            raise ProviderError(422, f"{ref.model_id} is not an embedding model")
        value = reg.litellm_id(conn.provider, ref.model_id)
        out[field] = value
        values[role] = value
        if role == "embedding":
            dims = KNOWN_DIMS.get(ref.model_id.rsplit("/", 1)[-1].split(":")[0])
            if dims:
                out["embed_dims"] = dims
            else:
                out.pop("embed_dims", None)
        group = GROUP_OF.get(role)
        if group and isinstance(out.get("models"), dict) and isinstance(out["models"].get(group), dict):
            out["models"] = {**out["models"], group: {**out["models"][group], "models": [value]}}
    return out, values


async def current(svc: Any, data: dict[str, Any], overrides: dict[str, Any]) -> ModelSelection:
    conns = await store.connections(svc.db)
    st = svc.settings
    picked: dict[str, SelectedModel] = {}
    for role, field in ROLE_FIELD.items():
        value = getattr(st, field)
        sel = SelectedModel(
            role=role,
            value=value,
            source=env_store.setting_source(f"AUOREO_{field.upper()}", field, data, overrides),
        )
        for conn in conns if value else []:
            prefix = reg.PROVIDERS[conn.provider].prefix if conn.provider in reg.PROVIDERS else None
            if prefix and value.startswith(prefix):
                mid = value[len(prefix) :]
                if await store.model(svc.db, conn.id, mid) is not None or sel.connection_id is None:
                    sel = sel.model_copy(
                        update={"connection_id": conn.id, "provider": conn.provider, "model_id": mid}
                    )
        picked[role] = sel
    return ModelSelection(**picked)


__all__ = ["current", "updates"]
