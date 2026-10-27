"""Every variable AUOREO reads, as metadata for the web's Settings → Environment page (`/api/env`).

Each `EnvVar` has a section, a description, a `kind` (`setting` = a `Settings` field stored in
the `app_settings` table of state.db, `secret` = a key stored write-only in secrets.db,
`passthrough` = read from the process environment only), a value type (bool|int|float|str|enum|
json, enum choices from the `Settings` annotation), the default, the `tier` (`env-only` for
security knobs that only the real environment may set, `core.env_rules`; else `anywhere`) and
whether a change applies live or needs a restart (`RESTART_REQUIRED`, each with the reason the
web shows). No `.env` / `.env.example` file is generated or read from it.
"""

from __future__ import annotations

import json
import re
import types
import typing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from auoreo.core.env_catalog_data import ITEMS, K, S, Sec
from auoreo.core.env_rules import is_protected
from auoreo.core.settings import Settings

Kind = Literal["setting", "secret", "passthrough"]
ValueType = Literal["bool", "int", "float", "str", "enum", "json"]
Tier = Literal["anywhere", "env-only"]
FIELDS = Settings.model_fields

RESTART_REQUIRED: dict[str, str] = {
    "home": "the runtime home is fixed for the process",
    "host": "the listening socket is bound at startup",
    "port": "the listening socket is bound at startup",
    "workers": "uvicorn forks its workers at startup",
    "redis_url": "the Redis event fan-out connects at startup",
    "db_encryption": "state.db is opened (and its cipher chosen) at startup",
    "server_password": "the SPA token injection and WS sessions capture it at startup",
    "gateway": "the gateway object is shared by memory, titles and aux calls from startup",
    "cors_origins": "the CORS middleware is built when the app is created",
    "mcp_server_enabled": "the /mcp endpoint is mounted when the app is created",
    "cli": "serve flags (WS auth, tunnel, mDNS) apply when `auoreo serve` starts",
    "cli2": "loop / heartbeat / worktree-gc timers start with the server",
    "logging": "log handlers are installed at startup",
    "otel_mode": "the FastAPI instrumentation is installed when the app is created",
    "memory": "the memory index (embedding model, vector dims) is opened at startup",
    "embed_model": "the memory index (embedding model, vector dims) is opened at startup",
    "embed_dims": "the memory index (embedding model, vector dims) is opened at startup",
    "i4": "memory scopes / tenants / re-embed jobs are set up at startup",
    "plugins_python": "plugin venvs are built with it; running plugins keep their interpreter",
    "voice_host_python": "the voice sidecar process keeps the interpreter it started with",
    "voice_host_backends": "the voice sidecar loads its inner backends when it starts",
    "i8": "the wake daemon and LiveKit worker start with the server",
    "livekit": "the LiveKit worker connects at startup",
    "i3": "the kanban dispatcher starts with the server",
    "automation_workers": "delivery workers are spawned at startup",
    "automation_start_delay_s": "only used once, when the server starts",
    "browser_executable_path": "the browser backend launches its binary once",
}
"""Fields only read at startup (verified against the code); everything else applies live."""
ENV_ONLY_RESTART = "security knobs are frozen from the real environment at startup"
SECRET_RE = re.compile(r"(_API_KEY|_TOKEN|_SECRET|_PASSWORD|_KEY|_TOKEN_ID)$")
PASSTHROUGH = frozenset(
    {"GH_HOST", "GH_CONFIG_DIR", "HTTPS_PROXY", "NO_PROXY", "SSL_CERT_FILE"}
    | {"UPDATE_SIGNING_KEY", "PLUGIN_SIGNING_KEY"}
)
"""Non-AUOREO names read straight from the process environment (gh CLI, release CI)."""
WEB_MANAGED = {"env": "managed in the Custom variables card"}


@dataclass(frozen=True)
class EnvVar:
    name: str
    """The environment variable (`AUOREO_MODEL`, `OPENAI_API_KEY`)."""
    section: str
    description: str
    kind: Kind
    type: ValueType
    tier: Tier
    field: str | None = None
    """The `Settings` field (= settings.json key) for `kind="setting"`."""
    choices: tuple[str, ...] = ()
    nullable: bool = False
    default: Any = None
    restart_reason: str | None = None

    @property
    def live(self) -> bool:
        return self.restart_reason is None


@dataclass(frozen=True)
class Section:
    id: str
    label: str
    title: str


def tier_tag(env: str) -> str:
    """`env-only` for security knobs (real environment only), else ''."""
    return "env-only" if is_protected(env) else ""


def _flat(name: str) -> tuple[list[Any], bool]:
    ann = FIELDS[name].annotation
    is_union = typing.get_origin(ann) in (typing.Union, types.UnionType)
    args = typing.get_args(ann) if is_union else (ann,)
    return [a for a in args if a is not type(None)], type(None) in args


def json_kind(name: str) -> str:
    """'' for scalars, 'JSON' for dict/list values, 'JSON object' for nested settings groups."""
    flat = [typing.get_origin(a) or a for a in _flat(name)[0]]
    if any(isinstance(a, type) and issubclass(a, BaseModel) for a in flat):
        return "JSON object"
    if any(a in (dict, list) for a in flat):
        return "JSON"
    return ""


_SCALAR: dict[Any, ValueType] = {bool: "bool", int: "int", float: "float", str: "str", Path: "str"}


def value_type(name: str) -> tuple[ValueType, tuple[str, ...], bool]:
    """`(type, enum choices, nullable)` of a settings field."""
    args, nullable = _flat(name)
    if json_kind(name):
        return "json", (), nullable
    if len(args) == 1 and typing.get_origin(args[0]) is Literal:
        return "enum", tuple(str(c) for c in typing.get_args(args[0])), nullable
    return _SCALAR.get(args[0], "str") if args else "str", (), nullable


def _kind_of(env: str) -> Kind:
    if env.startswith(("AUOREO_", "OTEL_")) or env in PASSTHROUGH:
        return "passthrough"
    return "secret"


def _tier(env: str) -> Tier:
    return "env-only" if is_protected(env) else "anywhere"


def _setting(item: S, section: str) -> EnvVar:
    env = f"AUOREO_{item.name.upper()}"
    vt, choices, nullable = value_type(item.name)
    f = FIELDS[item.name]
    try:
        default = f.default_factory() if f.default_factory is not None else f.default  # type: ignore[call-arg]
    except TypeError:
        default = None
    tier = _tier(env)
    reason = RESTART_REQUIRED.get(item.name) or (ENV_ONLY_RESTART if tier == "env-only" else None)
    return EnvVar(
        name=env,
        section=section,
        description=item.note or (f.description or "").split("\n")[0],
        kind="secret" if item.name == "server_password" else "setting",
        type=vt,
        tier=tier,
        field=item.name,
        choices=choices,
        nullable=nullable,
        default=json.loads(json.dumps(default, default=_jsonable)),
        restart_reason=reason,
    )


def _jsonable(v: Any) -> Any:
    return v.model_dump(mode="json") if isinstance(v, BaseModel) else str(v)


def _key(item: K, section: str, field: str | None = None) -> EnvVar:
    kind = _kind_of(item.env)
    tier = _tier(item.env)
    reason = (
        ENV_ONLY_RESTART
        if tier == "env-only"
        else ("read from the process environment" if kind == "passthrough" else None)
    )
    return EnvVar(
        name=item.env,
        section=section,
        description=item.note,
        kind=kind,
        type="str",
        tier=tier,
        field=field,
        restart_reason=reason if field is None else RESTART_REQUIRED.get(field, reason),
    )


@lru_cache(maxsize=1)
def catalog() -> tuple[tuple[Section, ...], tuple[EnvVar, ...]]:
    """`(sections, entries)` in catalog (display) order."""
    sections: list[Section] = []
    entries: list[EnvVar] = []
    current = ""
    for item in ITEMS:
        if isinstance(item, Sec):
            sections.append(Section(item.id, item.label, item.title))
            current = item.id
        elif isinstance(item, S):
            entries.append(_setting(item, current))
        elif isinstance(item, K):
            done = item.env.removeprefix("AUOREO_").lower()
            entries.append(_key(item, current, done if done in FIELDS else None))
    return tuple(sections), tuple(entries)


def entries() -> tuple[EnvVar, ...]:
    return catalog()[1]


def by_name() -> dict[str, EnvVar]:
    return {e.name: e for e in entries()}


def by_field() -> dict[str, EnvVar]:
    return {e.field: e for e in entries() if e.field}


def secret_names() -> frozenset[str]:
    """Catalog names whose value lives write-only in secrets.db."""
    return frozenset(e.name for e in entries() if e.kind == "secret" and e.field is None)


def is_secretish(name: str) -> bool:
    return name in secret_names() or bool(SECRET_RE.search(name.upper()))


__all__ = [
    "EnvVar",
    "RESTART_REQUIRED",
    "Section",
    "by_field",
    "by_name",
    "catalog",
    "entries",
    "is_secretish",
    "json_kind",
    "secret_names",
    "tier_tag",
    "value_type",
]
