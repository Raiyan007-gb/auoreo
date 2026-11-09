"""I5: `auoreo plugin get|set` — plugin config values in the settings store (`plugins.<name>`).

Secrets never travel through here: a key the plugin's `config_schema` marks secret
(`format: password|secret`, `writeOnly: true`, `x-secret: true`) or whose name looks like a
credential (`*token*`, `*secret*`, `*password*`, `*api_key*`, …) is refused by `set` and
masked by `get`. Secrets belong in the secret store (`auoreo secrets set …`) and reach the
plugin through `consumes: [secrets:<name>]`. Every `set` is validated against the schema
before anything is written.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from pathlib import Path
from typing import Any

from auoreo.core.settings import write_settings_value
from auoreo.harness.manifests import PluginManifest
from auoreo.plugins.connectors import file_config

SECRET_NAME = re.compile(r"token|secret|passw(or)?d|api[_-]?key|private[_-]?key|credential", re.I)
MASK = "********"


class PluginConfigError(ValueError):
    """Refused (`secret`, unknown key, schema violation); the message says why and what to do."""


def _prop(manifest: PluginManifest, key: str) -> dict[str, Any]:
    props = manifest.config_schema.get("properties", {})
    value = props.get(key) if isinstance(props, dict) else None
    return value if isinstance(value, dict) else {}


def is_secret(manifest: PluginManifest, key: str) -> bool:
    prop = _prop(manifest, key)
    if prop.get("format") in {"password", "secret"} or prop.get("writeOnly") or prop.get("x-secret"):
        return True
    return bool(SECRET_NAME.search(key))


def values(home: Path, overrides: dict[str, Any], manifest: PluginManifest) -> dict[str, Any]:
    """Effective config (`settings.plugins.<name>` merged with the file), secrets masked."""
    merged = {**overrides, **file_config(home, manifest.name)}
    return {k: (MASK if is_secret(manifest, k) else v) for k, v in sorted(merged.items())}


def parse_value(raw: str) -> Any:
    """JSON when it parses (`true`, `3`, `["a"]`, `{"x": 1}`), else the literal string."""
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def set_value(
    home: Path, manifest: PluginManifest, key: str, raw: str, agents: Collection[str] | None = None
) -> Any:
    from auoreo.plugins.host import validate_config

    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", key):
        raise PluginConfigError(f"invalid key '{key}'")
    if is_secret(manifest, key):
        raise PluginConfigError(
            f"'{key}' is a secret: store it with `auoreo secrets set <NAME>` and let the plugin "
            "consume it (secrets never go into the settings store)"
        )
    props = manifest.config_schema.get("properties", {})
    closed = manifest.config_schema.get("additionalProperties") is False
    if closed and key not in props:
        known = ", ".join(sorted(props)) or "none"
        raise PluginConfigError(f"'{manifest.name}' has no config key '{key}' (known: {known})")
    value = parse_value(raw)
    candidate = {**file_config(home, manifest.name), key: value}
    try:
        validate_config(manifest, candidate, agents)
    except Exception as exc:
        raise PluginConfigError(str(exc)) from exc
    write_settings_value(home, f"plugins.{manifest.name}.{key}", value)
    return value
