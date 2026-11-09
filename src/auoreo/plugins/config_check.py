"""Validation of a plugin's settings (`plugins.<name>`) against its manifest.

Two layers: the plugin's JSON Schema (`config_schema`), then, for a connector whose schema
declares `routes` (M7 W3), the route checks a schema cannot express: every regex compiles and
is short, `in_file` is absent, and the agent exists. Used when a value is saved, when the host
starts a plugin and by the offline listings. Imported through `auoreo.plugins.host`.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from auoreo.harness.manifests import PluginManifest
from auoreo.plugins import routes


class ConfigError(ValueError):
    pass


def validate_config(
    manifest: PluginManifest, config: dict[str, Any], agents: Collection[str] | None = None
) -> None:
    """Raise `ConfigError` naming the JSON pointer of the first (best) schema violation.

    `agents`: the known agent names, to refuse a route to a missing agent; None skips that one
    check (no agent registry at hand).
    """
    Draft202012Validator.check_schema(manifest.config_schema)
    err = best_match(Draft202012Validator(manifest.config_schema).iter_errors(config))
    if err is not None:
        pointer = "/" + "/".join(str(p) for p in err.absolute_path)
        raise ConfigError(f"settings plugins.{manifest.name} invalid at {pointer}: {err.message}")
    if "routes" in manifest.config_schema.get("properties", {}):
        try:
            routes.validate(config, agents)
        except routes.RouteError as exc:
            raise ConfigError(f"settings plugins.{manifest.name} invalid at {exc}") from None


__all__ = ["ConfigError", "validate_config"]
