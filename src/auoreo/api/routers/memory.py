from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.memory import curator as curator_mod
from auoreo.memory.service import MemoryService
from auoreo.schema.chat import CuratorReport, CuratorReportText, FactActionResult, FactFeedback, FactPin

router = APIRouter(prefix="/api/memory", tags=["memory"], dependencies=AUTH)


class SearchHit(BaseModel):
    kind: str
    id: int
    text: str
    score: float
    source: str | None = None


class FactIn(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    category: str = "user"


@router.get("/search", response_model=list[SearchHit])
async def search(q: str, svc: ServicesDep, limit: int = 8) -> list[SearchHit]:
    if svc.memory is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "memory is disabled")
    hits = await svc.memory.search(q, limit=min(max(limit, 1), 50))
    return [SearchHit(kind=h.kind, id=h.id, text=h.text, score=h.score, source=h.source) for h in hits]


@router.post("/facts", status_code=status.HTTP_201_CREATED)
async def add_fact(body: FactIn, svc: ServicesDep) -> dict[str, int | bool]:
    if svc.memory is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "memory is disabled")
    fact_id, created = await svc.memory.add_fact(body.content, category=body.category, source="user")
    return {"id": fact_id, "created": created}


def _memory(svc: ServicesDep) -> MemoryService:
    if svc.memory is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "memory is disabled")
    return svc.memory


def _found(ok: bool, fact_id: int) -> None:
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"fact {fact_id} not found")


@router.post("/facts/{fact_id}/forget", response_model=FactActionResult)
async def forget_fact(fact_id: int, svc: ServicesDep) -> FactActionResult:
    """Archive a fact (never deleted). `ok` is false when it was already archived."""
    mem = _memory(svc)
    _found(await mem.get_fact(fact_id) is not None, fact_id)
    return FactActionResult(id=fact_id, ok=await mem.forget(fact_id, actor="user"))


@router.post("/facts/{fact_id}/pin", response_model=FactActionResult)
async def pin_fact(fact_id: int, body: FactPin, svc: ServicesDep) -> FactActionResult:
    """Pin (or unpin with `pinned: false`): the curator never archives or merges pinned facts."""
    ok = await _memory(svc).pin(fact_id, body.pinned, actor="user")
    _found(ok, fact_id)
    return FactActionResult(id=fact_id, ok=ok)


@router.post("/facts/{fact_id}/feedback", response_model=FactActionResult)
async def feedback_fact(fact_id: int, body: FactFeedback, svc: ServicesDep) -> FactActionResult:
    """Trust feedback: +0.05 when helpful, -0.10 when not (clamped to 0-1)."""
    trust = await _memory(svc).feedback(fact_id, body.helpful)
    _found(trust is not None, fact_id)
    return FactActionResult(id=fact_id, ok=True, trust=trust)


@router.post("/curator/run", response_model=CuratorReport)
async def curator_run(svc: ServicesDep, dry_run: bool = False, force: bool = False) -> CuratorReport:
    """Run the deterministic curator now (`force` ignores the idle/interval guards)."""
    _memory(svc)
    return await curator_mod.get(svc).run(dry_run, force=force)


@router.get("/curator/report", response_model=CuratorReportText)
async def curator_report(svc: ServicesDep) -> CuratorReportText:
    """The most recent REPORT.md."""
    path = curator_mod.latest_report(svc.settings.home)
    if path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no curator report yet")
    return CuratorReportText(path=str(path), markdown=path.read_text(encoding="utf-8"))
