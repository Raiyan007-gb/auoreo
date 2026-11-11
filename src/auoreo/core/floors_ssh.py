"""Hardline floor on the mutating `auoreo ssh` operator commands (M7 hosts-b, O8).

`auoreo ssh add|test|keygen|deploy|rotate|import|rm` edit the host inventory, trust host
keys, write private keys into the secret store and rewrite remote `authorized_keys`. They
are operator commands: an agent's shell (`bash`, local `exec_command`) runs as the same OS
user and could otherwise call them. This floor refuses them in every tool shell, however
launched (`auoreo`, `uv run auoreo`, `python -m auoreo`, `uvx auoreo`, after `env`/`sudo`).

It is a speed bump, not a boundary: the same user can reach the files another way (a
script, a copied binary). The boundaries are the secret store's tool floor, the runtime-only
host files, the CLI-only trust decision and the per-host `ssh` grant.
"""

from __future__ import annotations

import os

MUTATING = frozenset({"add", "test", "keygen", "deploy", "rotate", "import", "rm"})
_ENTRY = frozenset({"auoreo", "auoreo.cli", "auoreo.cli.app", "auoreo.__main__"})
LAUNCHERS = ("auoreo", "uv", "uvx", "pipx", "python", "python3")


def _base(word: str) -> str:
    return os.path.basename(word).removesuffix(".exe")


def _after_entry(argv: list[str]) -> list[str] | None:
    """The arguments after the `auoreo` program in `argv` (None: not an auoreo call)."""
    for i, tok in enumerate(argv):
        word = _base(tok) if i == 0 or not tok.startswith("-") else tok
        if word in _ENTRY:
            return argv[i + 1 :]
    return None


def operator_cli(argv: list[str]) -> str | None:
    """Why `argv` may not run in a tool shell, or None."""
    head = _base(argv[0]) if argv else ""
    if head not in LAUNCHERS and not head.startswith("python"):
        return None
    rest = _after_entry(argv)
    if rest is None:
        return None
    words = [w for w in rest if not w.startswith("-")]
    if "ssh" not in words:
        return None
    after = words[words.index("ssh") + 1 :]
    if not after or after[0] not in MUTATING:
        return None
    return (
        f"`auoreo ssh {after[0]}` is an operator command (host inventory, host-key trust, keys); "
        "run it in your own terminal (hardline floor)"
    )
