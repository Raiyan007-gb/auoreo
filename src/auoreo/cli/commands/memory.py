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
        return await svc.memory.add_fact(content, category, source="user")

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


def _need(ok: bool, fact_id: int, what: str) -> None:
    if not ok:
        typer.echo(f"fact {fact_id} not found or already {what}", err=True)
        raise typer.Exit(1)


@app.command("forget")
def forget(fact_id: int, reason: str = "forgotten on request") -> None:
    """Archive a fact (never deleted; still visible with include_archived)."""

    async def go(svc: Services) -> bool:
        assert svc.memory is not None
        return await svc.memory.forget(fact_id, actor="user", reason=reason)

    _need(with_services(go), fact_id, "archived")
    typer.echo(f"fact {fact_id} archived")


@app.command("purge")
def purge(
    fact_id: int, yes: Annotated[bool, typer.Option("--yes", help="Confirm the hard delete.")] = False
) -> None:
    """Permanently delete a fact (the only hard delete; requires --yes)."""
    if not yes:
        typer.echo("refusing to purge without --yes (use `memory forget` to archive instead)", err=True)
        raise typer.Exit(2)

    async def go(svc: Services) -> bool:
        assert svc.memory is not None
        return await svc.memory.purge(fact_id, actor="user")

    _need(with_services(go), fact_id, "purged")
    typer.echo(f"fact {fact_id} purged")


def _set_pin(fact_id: int, pinned: bool) -> None:
    async def go(svc: Services) -> bool:
        assert svc.memory is not None
        return await svc.memory.pin(fact_id, pinned, actor="user")

    _need(with_services(go), fact_id, "deleted")
    typer.echo(f"fact {fact_id} {'pinned' if pinned else 'unpinned'}")


@app.command("pin")
def pin(fact_id: int) -> None:
    """Pin a fact: the curator never archives or merges it."""
    _set_pin(fact_id, True)


@app.command("unpin")
def unpin(fact_id: int) -> None:
    """Unpin a fact."""
    _set_pin(fact_id, False)


@app.command("feedback")
def feedback(
    fact_id: int,
    helpful: Annotated[bool, typer.Option("--helpful/--unhelpful", help="Trust +0.05 / -0.10.")],
) -> None:
    """Rate a recalled fact; trust is clamped to 0-1 (below min_trust it stops being recalled)."""

    async def go(svc: Services) -> float | None:
        assert svc.memory is not None
        return await svc.memory.feedback(fact_id, helpful)

    trust = with_services(go)
    _need(trust is not None, fact_id, "deleted")
    typer.echo(f"fact {fact_id} trust {trust:.2f}")


@app.command("show")
def show(fact_id: int) -> None:
    """Show one fact with its lifecycle columns."""

    async def go(svc: Services) -> dict[str, object] | None:
        assert svc.memory is not None
        return await svc.memory.get_fact(fact_id)

    row = with_services(go)
    _need(row is not None, fact_id, "deleted")
    dump_json(row)


graph_app = typer.Typer(no_args_is_help=True, help="GraphRAG-lite communities (deterministic, no LLM).")
app.add_typer(graph_app, name="graph")


@graph_app.command("build")
def graph_build() -> None:
    """Rebuild entity communities from the active facts (works even when graph_enabled is off)."""

    async def go(svc: Services) -> dict[str, object]:
        assert svc.memory is not None
        built = await svc.memory.build_graph()
        levels = sorted({c.level for c in built})
        return {
            "communities": len(built),
            "levels": {str(lvl): sum(1 for c in built if c.level == lvl) for lvl in levels},
            "facts": len({f for c in built for f in c.fact_ids}),
            "graph_enabled": svc.settings.memory.graph_enabled,
        }

    dump_json(with_services(go))


@graph_app.command("show")
def graph_show(
    level: Annotated[int | None, typer.Option(help="Only this level (1 or 2).")] = None,
    full: Annotated[bool, typer.Option("--full", help="Print whole summaries and entity lists.")] = False,
) -> None:
    """List stored communities with their entities and extractive summaries."""

    async def go(svc: Services) -> list[dict[str, object]]:
        assert svc.memory is not None
        return [
            {
                "id": c.id,
                "level": c.level,
                "entities": c.entities if full else c.entities[:10],
                "facts": len(c.fact_ids),
                "summary": c.summary if full else c.summary[:300],
            }
            for c in await svc.memory.communities(level)
        ]

    dump_json(with_services(go))
