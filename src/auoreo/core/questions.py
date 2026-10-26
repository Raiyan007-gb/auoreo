"""Questions from the agent to the user (`question.asked` → POST answer).

Spec §25.7 shapes: each question may carry `options[]`, `multiSelect` and `allowCustom`
(default true: a free-text answer is allowed, returned as `customText`). With
`allowCustom: false` only option labels are accepted. A per-call `timeout_s` overrides the
broker default; on timeout the caller gets `timed_out: true` and no responses.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

Emit = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class InvalidAnswer(ValueError):
    """An answer that the question does not allow (e.g. free text with `allowCustom: false`)."""


@dataclass
class PendingQuestion:
    request_id: str
    session_id: str
    questions: list[dict[str, Any]]
    future: asyncio.Future[dict[str, Any]] = field(repr=False)
    timeout_s: float = 0.0
    expires_at: float = 0.0


def normalize(questions: list[dict[str, Any]], responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`{question, answers[], customText?}` per question: option labels stay in `answers`;
    other text moves to `customText` when `allowCustom` (default true), else is refused."""
    out: list[dict[str, Any]] = []
    for i, q in enumerate(questions):
        r = responses[i] if i < len(responses) and isinstance(responses[i], dict) else {}
        labels = [str(o.get("label")) for o in q.get("options") or [] if isinstance(o, dict)]
        given = [str(a) for a in r.get("answers") or [] if str(a).strip()]
        if not given and str(r.get("answer") or "").strip():  # legacy flat `answer`
            given = [str(r["answer"]).strip()]
        picked = [a for a in given if a in labels] if labels else []
        free = [a for a in given if a not in labels] if labels else given
        custom = str(r.get("customText") or "").strip()
        if custom:
            free.append(custom)
        if free and q.get("allowCustom") is False:
            raise InvalidAnswer(f"question {i + 1} only accepts its options: {', '.join(labels)}")
        if len(picked) > 1 and not q.get("multiSelect"):
            raise InvalidAnswer(f"question {i + 1} is single-select")
        row: dict[str, Any] = {"question": q.get("question", r.get("question", "")), "answers": picked}
        if free:
            row["customText"] = "\n".join(dict.fromkeys(free))
            if not labels:  # free-form question: the text is the answer
                row["answers"] = [row["customText"]]
        row["answer"] = ", ".join([*picked, *free])  # legacy flat form
        out.append(row)
    return out


class QuestionBroker:
    def __init__(self, emit: Emit, timeout_s: float = 300.0) -> None:
        self._emit = emit
        self.timeout_s = timeout_s
        self._pending: dict[str, PendingQuestion] = {}

    def pending(self, session_id: str | None = None) -> list[PendingQuestion]:
        return [q for q in self._pending.values() if session_id in (None, q.session_id)]

    async def ask(
        self, session_id: str, questions: list[dict[str, Any]], timeout_s: float | None = None
    ) -> dict[str, Any]:
        wait = float(timeout_s) if timeout_s else self.timeout_s
        q = PendingQuestion(
            uuid.uuid4().hex,
            session_id,
            questions,
            asyncio.get_running_loop().create_future(),
            wait,
            time.time() + wait,
        )
        self._pending[q.request_id] = q
        await self._emit(
            session_id,
            "question.asked",
            {
                "request_id": q.request_id,
                "questions": questions,
                "timeout_s": wait,
                "expires_at": q.expires_at,
            },
        )
        try:
            responses = await asyncio.wait_for(asyncio.shield(q.future), wait)
            return {"responses": responses.get("responses", []), "timed_out": False}
        except TimeoutError:
            await self._emit(session_id, "question.answered", {"request_id": q.request_id, "timed_out": True})
            return {"responses": [], "timed_out": True}
        finally:
            self._pending.pop(q.request_id, None)

    async def answer(self, request_id: str, responses: list[dict[str, Any]]) -> bool:
        """Resolve a question; raises `InvalidAnswer` for a disallowed answer (the question stays open)."""
        q = self._pending.get(request_id)
        if q is None or q.future.done():
            return False
        clean = normalize(q.questions, responses)
        q.future.set_result({"responses": clean})
        await self._emit(q.session_id, "question.answered", {"request_id": request_id})
        return True
