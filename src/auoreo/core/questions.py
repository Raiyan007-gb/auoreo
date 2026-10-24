"""Questions from the agent to the user (`question.asked` → POST answer)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

Emit = Callable[[str, str, dict[str, Any]], Awaitable[None]]


@dataclass
class PendingQuestion:
    request_id: str
    session_id: str
    questions: list[dict[str, Any]]
    future: asyncio.Future[dict[str, Any]] = field(repr=False)


class QuestionBroker:
    def __init__(self, emit: Emit, timeout_s: float = 300.0) -> None:
        self._emit = emit
        self.timeout_s = timeout_s
        self._pending: dict[str, PendingQuestion] = {}

    def pending(self, session_id: str | None = None) -> list[PendingQuestion]:
        return [q for q in self._pending.values() if session_id in (None, q.session_id)]

    async def ask(self, session_id: str, questions: list[dict[str, Any]]) -> dict[str, Any]:
        q = PendingQuestion(
            uuid.uuid4().hex, session_id, questions, asyncio.get_running_loop().create_future()
        )
        self._pending[q.request_id] = q
        await self._emit(session_id, "question.asked", {"request_id": q.request_id, "questions": questions})
        try:
            responses = await asyncio.wait_for(asyncio.shield(q.future), self.timeout_s)
            return {"responses": responses.get("responses", []), "timed_out": False}
        except TimeoutError:
            return {"responses": [], "timed_out": True}
        finally:
            self._pending.pop(q.request_id, None)

    async def answer(self, request_id: str, responses: list[dict[str, Any]]) -> bool:
        q = self._pending.get(request_id)
        if q is None or q.future.done():
            return False
        q.future.set_result({"responses": responses})
        await self._emit(q.session_id, "question.answered", {"request_id": request_id})
        return True
