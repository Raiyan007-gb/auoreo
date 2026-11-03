"""Bundled MCP servers (`mcp/<name>.yaml`, shipped with `enabled: false`): copying one into the
runtime root (`<home>/mcp/`) with `enabled: true` turns it on. Shared by
`auoreo mcp add --bundle <name>` and `POST /api/harness/mcp/bundled/{name}`."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, cast

import yaml

from auoreo.core.paths import bundled_root
from auoreo.harness.discovery import Candidate, Root
from auoreo.harness.loader import load_mcp
from auoreo.harness.manifests import McpServerManifest


def bundled_folder() -> Path:
    return bundled_root() / "mcp"


def bundled_names() -> list[str]:
    return sorted(p.stem for p in bundled_folder().glob("*.yaml"))


def bundled_doc(bundle: str, name: str | None = None, *, enabled: bool = True) -> dict[str, Any]:
    """The bundled definition as a runtime document. Raises `KeyError` for an unknown name;
    names starting with `.` or `_`, or containing a path separator, are never looked up."""
    unsafe = bundle.startswith((".", "_")) or "/" in bundle or "\\" in bundle
    src = bundled_folder() / f"{bundle}.yaml"
    if unsafe or not src.is_file():
        known = ", ".join(bundled_names()) or "none"
        raise KeyError(f"no bundled MCP server '{bundle}' (bundled: {known})")
    doc = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    return {**doc, "name": name or doc.get("name", bundle), "enabled": enabled}


def validate_definition(doc: dict[str, Any]) -> McpServerManifest:
    """Load `doc` through the harness loader, exactly as discovery would (raises `LoadError`)."""
    name = str(doc["name"])
    with tempfile.TemporaryDirectory() as tmp:
        staged = Path(tmp) / f"{name}.yaml"
        staged.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        loaded = load_mcp(Candidate("mcp", name, staged, Root("runtime", Path(tmp))))
        return cast(McpServerManifest, loaded.manifest)


def write_definition(home: Path, doc: dict[str, Any], *, force: bool = False) -> Path:
    """Validate `doc` and write `<home>/mcp/<name>.yaml`. Raises `FileExistsError` when the
    file exists (unless `force`) and `LoadError` when the document does not load."""
    validate_definition(doc)
    dest = home / "mcp" / f"{doc['name']}.yaml"
    if dest.exists() and not force:
        raise FileExistsError(str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return dest
