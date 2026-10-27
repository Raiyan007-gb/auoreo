"""`/api/env` wire contract: the variable catalog with effective values, setting/secret writes,
custom variables, `.env` import and undo. No model here ever carries a secret value outbound:
secrets are reported as `is_set` + `updated_at` only."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

EnvSource = Literal["env", "db", "secrets", "override", "default"]


class EnvEntry(BaseModel):
    name: str = Field(description="The environment variable, e.g. `AUOREO_MODEL`")
    field: str | None = Field(default=None, description="Settings key (state.db) for `kind=setting`")
    description: str
    kind: Literal["setting", "secret", "passthrough"]
    type: Literal["bool", "int", "float", "str", "enum", "json"]
    choices: list[str] = Field(default_factory=list)
    nullable: bool = False
    tier: Literal["anywhere", "env-only"]
    source: EnvSource
    value: Any = Field(default=None, description="Effective value (redacted); always null for secrets")
    default: Any = None
    is_set: bool = Field(description="A non-default value is in effect (secrets: a value exists)")
    updated_at: datetime | None = Field(default=None, description="Secrets: last write")
    live: bool = Field(description="A change applies without a restart")
    restart_reason: str | None = None
    pending_restart: bool = Field(default=False, description="The stored value differs from the running one")
    writable: bool
    locked_reason: str | None = Field(default=None, description="Why the web cannot change it")


class EnvSectionOut(BaseModel):
    id: str
    label: str
    entries: list[EnvEntry]


class CustomVarOut(BaseModel):
    name: str
    secret: bool
    expose_to_shell: bool
    expose_to_agent: bool
    value: str | None = Field(default=None, description="Plain variables only; null for secrets")
    is_set: bool
    updated_at: str | None = None


class EnvListing(BaseModel):
    sections: list[EnvSectionOut]
    custom: list[CustomVarOut]
    pending_restart: list[str] = Field(default_factory=list)
    history: int = Field(description="Stored change sets available to undo")
    dotenv_path: str = Field(description="First `.env` found (home, then project) — import only")
    dotenv_paths: list[str] = Field(default_factory=list, description="Every `.env` the import would read")
    dotenv_exists: bool


class EnvSettingWrite(BaseModel):
    value: Any = Field(description="New value (JSON for json-typed entries); null resets to the default")


class EnvSecretWrite(BaseModel):
    value: str = Field(min_length=1, max_length=16_384)


class EnvSecretRotate(EnvSecretWrite):
    grace_s: int | None = Field(default=None, ge=0, le=30 * 86_400)


class EnvApplied(BaseModel):
    """What a write changed and how it applies."""

    name: str | None = None
    action: str
    changed: list[str] = Field(default_factory=list)
    live: list[str] = Field(default_factory=list)
    restart_required: list[str] = Field(default_factory=list)
    note: str


class CustomVarWrite(BaseModel):
    name: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    value: str | None = Field(default=None, max_length=8192, description="Omit on PUT to keep the value")
    secret: bool = False
    expose_to_shell: bool = False
    expose_to_agent: bool = False


class DotenvMove(BaseModel):
    name: str
    to: Literal["secrets", "settings", "skipped", "kept"]
    reason: str | None = None
    file: str = Field(default="", description="The `.env` file the name came from")
    provider: str | None = Field(default=None, description="Provider connection this key creates")


class DotenvProvider(BaseModel):
    """A provider connection created (or attempted) from an imported key."""

    provider: str
    ok: bool
    connection_id: int | None = None
    error: str | None = None


class DotenvImport(BaseModel):
    dry_run: bool
    moved: list[DotenvMove]
    counts: dict[str, int]
    note: str
    files: list[str] = Field(default_factory=list)
    providers: list[DotenvProvider] = Field(default_factory=list)


class DotenvImportRequest(BaseModel):
    dry_run: bool = False


class SettingsChanged(BaseModel):
    """`data` of a `settings.changed` event (names only)."""

    keys: list[str]
    live: list[str]
    restart_required: list[str]
    source: str


class SecretChanged(BaseModel):
    """`data` of a `secret.changed` event (never the value). `name` is null when an outside
    writer (`auoreo secrets set`) changed secrets.db."""

    name: str | None
    action: Literal["set", "rotate", "delete", "external", "import"]


__all__ = [
    "CustomVarOut",
    "CustomVarWrite",
    "DotenvImport",
    "DotenvImportRequest",
    "DotenvMove",
    "DotenvProvider",
    "EnvApplied",
    "EnvEntry",
    "EnvListing",
    "EnvSecretRotate",
    "EnvSecretWrite",
    "EnvSectionOut",
    "EnvSettingWrite",
    "SecretChanged",
    "SettingsChanged",
]
