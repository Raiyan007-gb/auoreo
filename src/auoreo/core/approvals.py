"""Human approval channel: `permission.requested` → reply `once|always|reject` (§19.2)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Reply = Literal["once", "always", "reject"]


@dataclass
class ApprovalOutcome:
    granted: bool
    reply: Reply | None
    message: str | None = None
    timed_out: bool = False


@dataclass
class Pending:
    request_id: str
    session_id: str
    tool: str
    permission: str
    pattern: str
    detail: str
    future: asyncio.Future[ApprovalOutcome] = field(repr=False)


Emit = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class ApprovalBroker:
    def __init__(self, emit: Emit, timeout_s: float = 300.0) -> None:
        self._emit = emit
        self.timeout_s = timeout_s
        self._pending: dict[str, Pending] = {}

    def pending(self, session_id: str | None = None) -> list[Pending]:
        return [p for p in self._pending.values() if session_id in (None, p.session_id)]

    async def request(
        self, session_id: str, tool: str, permission: str, pattern: str, detail: str
    ) -> ApprovalOutcome:
        p = Pending(
            uuid.uuid4().hex,
            session_id,
            tool,
            permission,
            pattern,
            detail,
            asyncio.get_running_loop().create_future(),
        )
        self._pending[p.request_id] = p
        await self._emit(
            session_id,
            "permission.requested",
            {
                "request_id": p.request_id,
                "tool": tool,
                "permission": permission,
                "pattern": pattern,
                "detail": detail,
            },
        )
        try:
            return await asyncio.wait_for(asyncio.shield(p.future), timeout=self.timeout_s)
        except TimeoutError:
            # Silence is never consent (§19.2).
            return ApprovalOutcome(False, None, timed_out=True)
        finally:
            self._pending.pop(p.request_id, None)

    async def reply(
        self,
        request_id: str,
        reply: Reply,
        message: str | None = None,
        on_always: Callable[[Pending], list[str]] | None = None,
    ) -> bool:
        """Resolve a request. `reject` cascades to all pendings of the same session;
        `always` auto-resolves pendings that `on_always` reports as now allowed."""
        p = self._pending.get(request_id)
        if p is None or p.future.done():
            return False
        p.future.set_result(ApprovalOutcome(reply != "reject", reply, message))
        await self._emit(p.session_id, "permission.replied", {"request_id": request_id, "reply": reply})
        if reply == "reject":
            for other in self.pending(p.session_id):
                if other.request_id != request_id and not other.future.done():
                    other.future.set_result(ApprovalOutcome(False, "reject", None))
                    await self._emit(
                        other.session_id,
                        "permission.replied",
                        {"request_id": other.request_id, "reply": "reject", "cascade": True},
                    )
        elif reply == "always" and on_always is not None:
            for rid in on_always(p):
                auto = self._pending.get(rid)
                if auto and not auto.future.done():
                    auto.future.set_result(ApprovalOutcome(True, "always"))
                    await self._emit(
                        auto.session_id,
                        "permission.replied",
                        {"request_id": rid, "reply": "always", "auto": True},
                    )
        return True
