"""H4: `auoreo completion bash|zsh|fish|pwsh` — print the shell completion script (spec §18).

The scripts are Typer/Click's own (`_AUOREO_COMPLETE` protocol), the same ones
`--install-completion` writes; printing them lets users source or package them."""

from __future__ import annotations

from typing import Annotated

import typer

PROG = "auoreo"
COMPLETE_VAR = "_AUOREO_COMPLETE"
SHELLS = {"bash": "bash", "zsh": "zsh", "fish": "fish", "pwsh": "pwsh", "powershell": "powershell"}
HINTS = {
    "bash": 'eval "$(auoreo completion bash)"   # in ~/.bashrc',
    "zsh": 'eval "$(auoreo completion zsh)"    # in ~/.zshrc (after compinit)',
    "fish": "auoreo completion fish > ~/.config/fish/completions/auoreo.fish",
    "pwsh": "auoreo completion pwsh | Out-String | Invoke-Expression   # in $PROFILE",
    "powershell": "auoreo completion powershell | Out-String | Invoke-Expression   # in $PROFILE",
}


def completion(
    shell: Annotated[str, typer.Argument(help="bash | zsh | fish | pwsh (alias: powershell)")],
    hint: Annotated[bool, typer.Option("--hint", help="Print how to install it instead.")] = False,
) -> None:
    """Print the completion script for SHELL (source it, or save it where your shell looks)."""
    from typer._completion_shared import get_completion_script

    key = SHELLS.get(shell.lower())
    if key is None:
        typer.echo(f"unsupported shell '{shell}' (choose: bash, zsh, fish, pwsh)", err=True)
        raise typer.Exit(2)
    if hint:
        typer.echo(HINTS[key])
        return
    typer.echo(get_completion_script(prog_name=PROG, complete_var=COMPLETE_VAR, shell=key))
