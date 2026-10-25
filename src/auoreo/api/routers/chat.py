from fastapi import APIRouter, HTTPException, Request, status

from auoreo.api.deps import AUTH, IDEMPOTENT, ServicesDep, admit_request
from auoreo.runtime.inbox import InboxFull
from auoreo.schema.chat import ChatAccepted, ChatRequest

router = APIRouter(prefix="/api", tags=["chat"], dependencies=AUTH)


def queue_full(exc: InboxFull) -> HTTPException:
    """Inbox overflow → 429 with a retry hint (header + body)."""
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        {"message": str(exc), "retry_after_s": exc.retry_after_s},
        headers={"Retry-After": str(exc.retry_after_s)},
    )


@router.post(
    "/chat",
    dependencies=[IDEMPOTENT],
    response_model=ChatAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        200: {"model": ChatAccepted, "description": "Duplicate request (deduped)"},
        409: {"description": "A request with the same idempotency key is still running"},
        429: {"description": "Session queue full (`Retry-After`)"},
    },
)
async def chat(body: ChatRequest, svc: ServicesDep, request: Request) -> ChatAccepted:
    """Queue a message. Returns immediately; follow progress on `GET /api/events`.
    `Idempotency-Key` header (or body `request_id`) makes retries safe."""
    if body.request_id is not None and "idempotency-key" not in request.headers:
        await admit_request(request, body.request_id)
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
    except InboxFull as exc:
        raise queue_full(exc) from exc
    return ChatAccepted(session_id=session_id, run_id=run_id)
