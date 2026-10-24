from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from auoreo.api.deps import AUTH, ServicesDep

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
    fact_id, created = await svc.memory.add_fact(body.content, category=body.category)
    return {"id": fact_id, "created": created}
