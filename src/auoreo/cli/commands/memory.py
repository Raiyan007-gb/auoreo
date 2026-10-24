from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from auoreo.cli.common import dump_json, with_services
from auoreo.core.services import Services

app = typer.Typer(no_args_is_help=True)


@app.command("search")
def search(query: str, limit: int = 8) -> None:
    """Hybrid (FTS + vector) search over facts and documents."""

    async def go(svc: Services) -> list[dict[str, object]]:
        assert svc.memory is not None
        return [
            {"cite": h.citation, "score": round(h.score, 5), "text": h.text[:300]}
            for h in await svc.memory.search(query, limit)
        ]

    dump_json(with_services(go))


@app.command("add")
def add(content: str, category: str = "user") -> None:
    """Add a fact."""

    async def go(svc: Services) -> tuple[int, bool]:
        assert svc.memory is not None
        return await svc.memory.add_fact(content, category)

    fid, created = with_services(go)
    typer.echo(f"fact {fid} {'added' if created else 'already existed'}")


@app.command("ingest")
def ingest(path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    """Chunk, embed and store a text/markdown file as knowledge."""

    async def go(svc: Services) -> int:
        assert svc.memory is not None
        return await svc.memory.ingest(path.read_text(encoding="utf-8"), str(path.resolve()))

    typer.echo(f"{with_services(go)} new chunks")


@app.command("stats")
def stats() -> None:
    """Show memory counts and whether the vector index is active."""

    async def go(svc: Services) -> dict[str, object]:
        assert svc.memory is not None
        return await svc.memory.stats()

    dump_json(with_services(go))
