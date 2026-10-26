"""Pre-permission shell gate (spec §19.4, §19.7, §25.10).

Runs on every argument that holds shell text: a `bash`-permission tool's `pattern_arg`
(bash, exec_command, pwsh, terminal_pty) plus the manifest's `shell_args` (monitor `cmd`,
write_stdin / terminal_pty_send `chars`).

* An oversized payload is never run: it is saved to `<home>/blocked/blocked-<ts>.sh`
  (0600) and the tool answers with a `bash <file>` recovery hint. Running the saved file
  later always asks (`core.floors`), so it is never a blind retry.
* A `path` argument naming `.env` / `secrets.db` is refused for every tool (§19.7).
* For tools whose permission is not `bash` the hardline + secret-path floors run here
  (for `bash` tools they run inside `PermissionEngine.evaluate`).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auoreo.core.floors_secrets import is_secret_path

if TYPE_CHECKING:
    from auoreo.core.permissions import PermissionEngine
    from auoreo.harness.loader import ToolDef
    from auoreo.tools.context import ToolContext

DEFAULT_LIMIT = 16_384


def shell_texts(tool: ToolDef, args: dict[str, Any]) -> list[tuple[str, str]]:
    """`(arg name, value)` for every non-empty shell-text argument of this call."""
    m = tool.manifest
    names = list(m.shell_args)
    if m.permission_name == "bash" and m.pattern_arg:
        names.insert(0, m.pattern_arg)
    return [(n, args[n]) for n in dict.fromkeys(names) if isinstance(args.get(n), str) and args[n]]


def save_blocked(home: Path, text: str, suffix: str = ".sh") -> Path:
    """Write the payload owner-only under `<home>/blocked/` and return its path."""
    folder = home / "blocked"
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        folder.chmod(0o700)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = folder / f"blocked-{stamp}{suffix}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def check(
    tool: ToolDef,
    args: dict[str, Any],
    ctx: ToolContext,
    engine: PermissionEngine,
    *,
    home: Path,
    limit: int = DEFAULT_LIMIT,
) -> tuple[str, str] | None:
    """`(audit reason, model-facing message)` when the call must not run, else None."""
    path = args.get("path")  # e.g. monitor following `.env`
    if isinstance(path, str) and path and is_secret_path(str(ctx.resolve_path(path))):
        return "floor", "permission denied (floor: secret-path floor). Do not retry unchanged."
    for name, text in shell_texts(tool, args):
        size = len(text.encode("utf-8"))
        if size > limit:
            pwsh = tool.name == "pwsh"
            saved = save_blocked(home, text, ".ps1" if pwsh else ".sh")
            run = f"pwsh -File {saved}" if pwsh else f"bash {saved}"
            return (
                "oversized_payload",
                f"refused: the inline `{name}` payload is {size} bytes (limit {limit}). It was "
                f"saved, not run, to {saved}. Review it, then run `{run}` (that always asks for "
                "approval). Do not retry the inline payload.",
            )
        if tool.manifest.permission_name != "bash":
            why = engine.floor("bash", text, interactive=ctx.interactive)
            if why is not None:
                return "floor", f"permission denied (floor: {why}). Do not retry unchanged."
    return None
