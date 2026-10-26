"""I5: global `auoreo --json` / `--mini` output modes (spec §18 `oreo [--profile] [--json] …`).

`--json` turns on every sub-command's own `--json` flag (list/get/status commands print JSON):
the root callback walks the click command tree and pre-sets those flags through click's
`default_map`, so a command's explicit `--json` and the global one behave the same and no
command needs to know about the global flag. Commands without a local flag ask `want_json()`.

`--mini` is compact output: JSON on one line, no colour or markup highlighting, soft-wrapped
text (good for pipes, narrow terminals and logs).
"""

from __future__ import annotations

import json
from typing import Any

_state = {"json": False, "mini": False}
JSON_OPTS = ("--json",)


def want_json(local: bool = False) -> bool:
    return local or _state["json"]


def mini() -> bool:
    return _state["mini"]


def json_defaults(cmd: Any) -> dict[str, Any]:
    """Nested `default_map` setting every `--json` flag below `cmd` to True. Duck-typed: Typer
    ships its own vendored click classes."""
    out: dict[str, Any] = {}
    for p in getattr(cmd, "params", []):
        if any(o in JSON_OPTS for o in getattr(p, "opts", ())) and p.name:
            out[p.name] = True
    if isinstance(commands := getattr(cmd, "commands", None), dict):
        for name, sub in commands.items():
            nested = json_defaults(sub)
            if nested:
                out[name] = nested
    return out


def configure(ctx: Any, *, as_json: bool, compact: bool) -> None:
    """Called from the root callback before the sub-command's context is built."""
    _state["json"], _state["mini"] = as_json, compact
    if as_json:
        ctx.default_map = {**json_defaults(ctx.command), **(ctx.default_map or {})}
    _style(compact)


_ORIG: dict[int, tuple[bool, bool, bool]] = {}


def _style(compact: bool) -> None:
    from auoreo.cli.common import console, err_console

    for c in (console, err_console):
        orig = _ORIG.setdefault(id(c), (c.no_color, c.soft_wrap, c._highlight))
        c.no_color, c.soft_wrap, c._highlight = (True, True, False) if compact else orig


def reset() -> None:
    """Back to defaults (tests invoke the app many times in one process)."""
    _state["json"] = _state["mini"] = False
    _style(False)


def dumps(obj: Any) -> str:
    if _state["mini"]:
        return json.dumps(obj, separators=(",", ":"), default=str)
    return json.dumps(obj, indent=2, default=str)
