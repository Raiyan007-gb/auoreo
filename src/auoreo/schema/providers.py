"""`/api/providers` + `/api/models/selection` wire contract (Settings → Models).

No model here ever carries an API key outbound: a connection reports its status, model count and
the secret NAME only; `ProviderCreate.key` / `ProviderUpdate.key` are inbound, write-only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ConnStatus = Literal["ok", "invalid", "error", "unchecked"]
ModelKind = Literal["chat", "embedding", "other"]


class ProviderSpecOut(BaseModel):
    id: str
    name: str
    prefix: str = Field(description="LiteLLM model prefix, e.g. `openai/`")
    needs_key: bool
    needs_base_url: bool
    needs_api_version: bool
    manual_models: bool = Field(description="Azure: deployment names are entered by hand")
    default_base_url: str | None = None
    default_api_version: str | None = None
    docs_url: str = ""


class ProviderDetectRequest(BaseModel):
    key: str = Field(default="", max_length=4096)


class ProviderDetectResult(BaseModel):
    candidates: list[str]
    confident: bool
    providers: list[ProviderSpecOut] = Field(description="Specs of the candidates, in order")


class ProviderAdopt(BaseModel):
    """Connect a provider whose key is already stored (no key in the body, nothing is copied)."""

    provider: str = Field(max_length=64)


class ProviderCreate(BaseModel):
    provider: str = Field(max_length=64)
    key: str | None = Field(default=None, max_length=4096, description="Write-only; never returned")
    base_url: str | None = Field(default=None, max_length=1024)
    api_version: str | None = Field(default=None, max_length=64)
    label: str | None = Field(default=None, max_length=128)
    deployments: list[str] = Field(default_factory=list, max_length=200, description="Azure only")


class ProviderUpdate(BaseModel):
    key: str | None = Field(default=None, max_length=4096, description="Replace the key (validated)")
    label: str | None = Field(default=None, max_length=128)
    base_url: str | None = Field(default=None, max_length=1024)
    api_version: str | None = Field(default=None, max_length=64)
    deployments: list[str] | None = Field(default=None, max_length=200, description="Azure only")


class ProviderConnectionOut(BaseModel):
    id: int
    provider: str
    provider_name: str
    label: str
    secret_name: str | None = Field(default=None, description="Where the key lives (name only)")
    base_url: str | None = None
    api_version: str | None = None
    status: ConnStatus
    last_error: str | None = Field(default=None, description="The provider's last error, redacted")
    model_count: int
    checked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ProviderList(BaseModel):
    connections: list[ProviderConnectionOut]
    providers: list[ProviderSpecOut] = Field(description="Every provider that can be connected")


class ProviderModelOut(BaseModel):
    model_id: str
    litellm_id: str = Field(description="What the gateway is called with: `<prefix><model_id>`")
    name: str
    kind: ModelKind
    context_window: int | None = None
    input_price: float | None = Field(default=None, description="USD per 1M input tokens")
    output_price: float | None = Field(default=None, description="USD per 1M output tokens")
    capabilities: list[str] = Field(default_factory=list)


class ProviderModels(BaseModel):
    connection_id: int
    status: ConnStatus
    fetched_at: datetime | None = None
    stale: bool = Field(default=False, description="Older than the cache TTL (1 h)")
    error: str | None = None
    models: list[ProviderModelOut]


class ProviderResult(BaseModel):
    """Answer of add / update / refresh: the connection, or the provider's error."""

    ok: bool
    connection: ProviderConnectionOut | None = None
    status: ConnStatus
    error: str | None = None
    model_count: int = 0


class ModelRef(BaseModel):
    connection_id: int
    model_id: str = Field(max_length=255)


class ModelSelectionWrite(BaseModel):
    main: ModelRef | None = None
    small: ModelRef | None = None
    embedding: ModelRef | None = None


class SelectedModel(BaseModel):
    role: Literal["main", "small", "embedding"]
    value: str | None = Field(description="The effective setting, e.g. `openai/gpt-5`")
    connection_id: int | None = None
    provider: str | None = None
    model_id: str | None = None
    source: str = Field(description="env | db | override | default")


class ModelSelection(BaseModel):
    main: SelectedModel
    small: SelectedModel
    embedding: SelectedModel


class ModelSelectionApplied(BaseModel):
    selection: ModelSelection
    changed: list[str]
    live: list[str]
    restart_required: list[str]
    note: str


class ProvidersChanged(BaseModel):
    """`data` of a `providers.changed` event (ids and names only)."""

    action: Literal["add", "update", "delete", "refresh"]
    connection_id: int
    provider: str
    status: ConnStatus


class ModelsChanged(BaseModel):
    """`data` of a `models.changed` event."""

    roles: list[str]
    values: dict[str, str | None]


__all__ = [
    "ModelRef",
    "ModelSelection",
    "ModelSelectionApplied",
    "ModelSelectionWrite",
    "ModelsChanged",
    "ProviderConnectionOut",
    "ProviderCreate",
    "ProviderDetectRequest",
    "ProviderDetectResult",
    "ProviderList",
    "ProviderModelOut",
    "ProviderModels",
    "ProviderResult",
    "ProviderSpecOut",
    "ProviderUpdate",
    "ProvidersChanged",
    "SelectedModel",
]
