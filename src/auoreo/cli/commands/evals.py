"""H4: `auoreo eval memory [ARGS...]` — wraps `scripts/eval_memory.py` (spec §25.8).

The eval script ships with the source checkout (it reads `tests/fixtures/memory_golden.jsonl`);
wheel and binary installs do not carry it, which is reported instead of failing obscurely.
Extra arguments pass straight through (`--backend none --json`, `--graph`, `--hyde`, …)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import typer

from auoreo.core.paths import bundled_root

app = typer.Typer(no_args_is_help=True)
PASSTHROUGH = {"allow_extra_args": True, "ignore_unknown_options": True}


def script_path() -> Path:
    return bundled_root() / "scripts" / "eval_memory.py"


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("auoreo_eval_memory", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@app.command("memory", context_settings=PASSTHROUGH)
def memory(ctx: typer.Context) -> None:
    """Memory retrieval eval: recall@10 / nDCG@10 / p95 / $ per 1k per rerank backend.

    Pass `-- --help` to see the script's own options."""
    path = script_path()
    if not path.is_file():
        typer.echo(
            f"memory eval needs a source checkout: {path} not found "
            "(set AUOREO_BUNDLED_ROOT to a checkout of the repository)",
            err=True,
        )
        raise typer.Exit(1)
    try:
        code = _load(path).main(list(ctx.args))
    except SystemExit as exc:  # argparse --help / usage errors
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 2)
    raise typer.Exit(int(code or 0))
