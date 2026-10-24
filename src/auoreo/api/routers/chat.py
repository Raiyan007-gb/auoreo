from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.idempotency import valid_key
from auoreo.schema.chat import ChatAccepted, ChatRequest

router = APIRouter(prefix="/api", tags=["chat"], dependencies=AUTH)


@router.post(
    "/chat",
    response_model=ChatAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"model": ChatAccepted, "description": "Duplicate request (deduped)"}},
)
async def chat(
    body: ChatRequest,
    svc: ServicesDep,
    response: Response,
    idempotency_key: Annotated[str | None, Header()] = None,
) -> ChatAccepted:
    """Queue a message. Returns immediately; follow progress on `GET /api/events`."""
    key = idempotency_key or body.request_id
    if key is not None:
        if not valid_key(key):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "idempotency key must be a UUID")
        if (prior := await svc.idempotency.lookup(key)) is not None:
            response.status_code = status.HTTP_200_OK
            return ChatAccepted(**{**prior, "deduped": True})
    try:
        session_id = body.session_id
        if session_id is None:
            row = await svc.create_session(agent=body.agent, workflow=body.workflow, model=body.model)
            session_id = row.id
        elif await svc.get_session(session_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
        run_id = await svc.coordinator.submit(
            session_id,
            body.message,
            body.mode,
            agent=body.agent,
            workflow=body.workflow,
            model=body.model,
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc).strip("'\"")) from exc
    result = {"session_id": session_id, "run_id": run_id}
    if key is not None:
        stored = await svc.idempotency.store(key, result)
        if stored != result:
            response.status_code = status.HTTP_200_OK
            return ChatAccepted(**{**stored, "deduped": True})
    return ChatAccepted(**result)
