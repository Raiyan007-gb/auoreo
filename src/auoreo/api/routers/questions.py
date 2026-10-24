from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from auoreo.api.deps import AUTH, ServicesDep

router = APIRouter(prefix="/api/questions", tags=["questions"], dependencies=AUTH)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    responses: list[dict[str, Any]]


class PendingQuestionOut(BaseModel):
    request_id: str
    session_id: str
    questions: list[dict[str, Any]]


@router.get("/pending", response_model=list[PendingQuestionOut])
async def pending(svc: ServicesDep, session_id: str | None = None) -> list[PendingQuestionOut]:
    return [
        PendingQuestionOut(request_id=q.request_id, session_id=q.session_id, questions=q.questions)
        for q in svc.questions.pending(session_id)
    ]


@router.post("/{request_id}/answer")
async def answer(request_id: str, body: Answer, svc: ServicesDep) -> dict[str, bool]:
    if not await svc.questions.answer(request_id, body.responses):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no pending question with that id")
    return {"ok": True}
