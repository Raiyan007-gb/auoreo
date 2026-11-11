"""`auoreo eval memory [ARGS...]` (H4) and `auoreo eval agents [NAME...]` (M7) — wrap
`scripts/eval_memory.py` (spec §25.8) and `scripts/eval_agents.py`.

The eval scripts ship with the source checkout (the memory eval reads
`tests/fixtures/memory_golden.jsonl`); wheel and binary installs do not carry them, which is
reported instead of failing obscurely. Extra arguments pass straight through (`--backend none
--json`, `--graph`, `--hyde`, …; `--root DIR`, `--json`, `--gateway fake|live`)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import typer

from auoreo.core.paths import bundled_root

app = typer.Typer(no_args_is_help=True)
PASSTHROUGH = {"allow_extra_args": True, "ignore_unknown_options": True}


def script_path(name: str = "eval_memory.py") -> Path:
    return bundled_root() / "scripts" / name


def _load(path: Path) -> ModuleType:
    name = f"auoreo_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses in the script resolve their module by name
    spec.loader.exec_module(module)
    return module


def _run(what: str, script: str, args: list[str]) -> None:
    path = script_path(script)
    if not path.is_file():
        typer.echo(
            f"{what} eval needs a source checkout: {path} not found "
            "(set AUOREO_BUNDLED_ROOT to a checkout of the repository)",
            err=True,
        )
        raise typer.Exit(1)
    try:
        code = _load(path).main(args)
    except SystemExit as exc:  # argparse --help / usage errors
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 2)
    raise typer.Exit(int(code or 0))


@app.command("memory", context_settings=PASSTHROUGH)
def memory(ctx: typer.Context) -> None:
    """Memory retrieval eval: recall@10 / nDCG@10 / p95 / $ per 1k per rerank backend.

    Pass `-- --help` to see the script's own options."""
    _run("memory", "eval_memory.py", list(ctx.args))


@app.command("agents", context_settings=PASSTHROUGH)
def agents(ctx: typer.Context) -> None:
    """Agent profile evals: run `agents/<name>/evals/*.yaml` on the fake gateway (exit 1 on a
    failing case, 2 on a malformed one). No CI gate. Pass `-- --help` for the options."""
    _run("agents", "eval_agents.py", list(ctx.args))
