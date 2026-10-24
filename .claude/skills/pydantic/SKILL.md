---
name: pydantic
description: Define contract models and validation with Pydantic v2 — BaseModel, Field, discriminated unions, serialization, and settings. Use when writing agent_schema models in this repo.
---

# Pydantic v2 — schema source of truth (agent_schema)

Docs: https://docs.pydantic.dev · Context7: `/websites/pydantic_dev_validation` (v2 docs; repo: `/pydantic/pydantic`), settings: `/pydantic/pydantic-settings`. Pydantic v2 is the contract layer: `agent_schema` models → FastAPI request/response → OpenAPI → generated SDKs (plan.md Part B + K).

## Base models

```python
from pydantic import BaseModel, ConfigDict, Field


class Agent(BaseModel):
    model_config = ConfigDict(extra="forbid")  # strict: reject unknown keys in API payloads

    id: str = Field(pattern=r"^agent_[a-z0-9]+$")
    name: str
    mode: Literal["primary", "subagent", "all"] = "primary"
    steps: int | None = Field(default=None, ge=1)
    system: str | None = None
    permissions: PermissionRuleset | None = None
```

- Field validation via type hints + `Field(...)` constraints; custom logic in `@field_validator` or `@model_validator`.
- Keep models free of I/O; they are pure contracts (parse → validate → serialize).

## Discriminated unions (message / tool-state tagging)

The event + message model is a **tagged union** (plan.md Part G `message.*`, ToolState). Use Pydantic v2 `Field(discriminator=...)`:

```python
class ToolStatePending(BaseModel):
    status: Literal["pending"]
    input: str


class ToolStateRunning(BaseModel):
    status: Literal["running"]
    input: dict[str, Any]
    content: list[ToolContent]


class ToolStateCompleted(BaseModel):
    status: Literal["completed"]
    result: Any | None = None
    output_paths: list[str] | None = None


class ToolStateError(BaseModel):
    status: Literal["error"]
    error: ToolError


ToolState = Annotated[
    ToolStatePending | ToolStateRunning | ToolStateCompleted | ToolStateError,
    Field(discriminator="status"),
]
```

- As a field on a model, `Model.model_validate({...})` dispatches to the right variant automatically;
  `model_dump(mode="json")` serializes for SSE/API.
- `ToolState` itself is an `Annotated` alias, not a `BaseModel` — validate it standalone with
  `TypeAdapter(ToolState).validate_python(...)` / `.dump_python(..., mode="json")` (build the adapter once, module-level).
- This mirrors opencode's `Schema.Struct` tagged-union approach for `session-message` + `tool-state`.

## Settings / env

Use `pydantic-settings` for server config from env (secrets stay in env, plan.md Part K):

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_PLATFORM_", env_file=".env", extra="ignore")
    server_password: str | None = None  # ← AGENT_PLATFORM_SERVER_PASSWORD (auth-oauth skill)
    database_url: str = "sqlite+aiosqlite:///./agent.db"
    cors_origins: list[str] = []  # env value must be JSON: '["http://localhost:3000"]'
```

- Complex fields (`list`, `dict`, sub-models) are JSON-decoded from env; a non-JSON value raises
  `SettingsError`. For comma-separated input use `Annotated[list[str], NoDecode, BeforeValidator(split)]`
  (`from pydantic_settings import NoDecode`).

## Correctness notes for this repo

- **Discriminator fields must be `Literal`** on every variant or validation fails — keep the `status`/`type` tags exact (they're the wire contract for the event manifest).
- FastAPI derives OpenAPI from these models; run the `sdk.yml` guard after any schema change (plan.md Part K).
- Don't mutate shared models between requests; prefer `model_copy(update=...)` for derived states
  (note: `update` values are **not validated** — only pass trusted, already-typed data; nested models are shared unless `deep=True`).
- v2 vs v1: no `Config` class — use `model_config = ConfigDict(...)`; no `.dict()`/`.parse_obj()` — use `model_dump()`/`model_validate()`.