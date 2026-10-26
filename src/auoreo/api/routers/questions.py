from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.questions import InvalidAnswer

router = APIRouter(prefix="/api/questions", tags=["questions"], dependencies=AUTH)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    responses: list[dict[str, Any]]


class PendingQuestionOut(BaseModel):
    request_id: str
    session_id: str
    questions: list[dict[str, Any]]
    timeout_s: float | None = None
    expires_at: float | None = None


@router.get("/pending", response_model=list[PendingQuestionOut])
async def pending(svc: ServicesDep, session_id: str | None = None) -> list[PendingQuestionOut]:
    return [
        PendingQuestionOut(
            request_id=q.request_id,
            session_id=q.session_id,
            questions=q.questions,
            timeout_s=q.timeout_s or None,
            expires_at=q.expires_at or None,
        )
        for q in svc.questions.pending(session_id)
    ]


@router.post("/{request_id}/answer")
async def answer(request_id: str, body: Answer, svc: ServicesDep) -> dict[str, bool]:
    try:
        ok = await svc.questions.answer(request_id, body.responses)
    except InvalidAnswer as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    if not ok:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no pending question with that id")
    return {"ok": True}
