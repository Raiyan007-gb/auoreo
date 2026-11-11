"""Human approval channel: `permission.requested` → reply `once|always|reject` (§19.2)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from auoreo.core.permissions import PermissionEngine

Reply = Literal["once", "always", "reject"]


@dataclass
class ApprovalOutcome:
    granted: bool
    reply: Reply | None
    message: str | None = None
    timed_out: bool = False
    request_id: str | None = None
    """Links the decision to its `permission.*` events and audit row."""
    updated_input: dict[str, Any] | None = None
    """`allow{updated_input}` (spec §5.2): the human edited the tool arguments (I5)."""


@dataclass
class Pending:
    request_id: str
    session_id: str
    tool: str
    permission: str
    pattern: str
    detail: str
    future: asyncio.Future[ApprovalOutcome] = field(repr=False)
    patterns: list[str] = field(default_factory=list)
    """Checked scope: what this approval covers (§19.2)."""
    always: list[str] = field(default_factory=list)
    """Persist scope for an `always` reply; must be a subset of `patterns`."""
    cwd: str | None = None
    risk: str = "medium"
    one_shot: bool = False
    """No `always` (sandbox escalation, single-use grants): an `always` reply counts as `once`."""
    editable: bool = False
    """The reply may carry `updated_input` (a tool call's arguments, I5)."""
    input: dict[str, Any] | None = None
    """The arguments shown in the card's editor (redacted; editable requests only)."""
    justification: str | None = None
    """Why the agent asks (sandbox escalation `justification`): shown on the card; the audit
    row keeps its hash (`justificationHash`), never the text."""


class EditRefused(ValueError):
    """`updated_input` on a request that does not accept edits (I5)."""


class ScopeError(ValueError):
    """`always[]` is not a subset of `patterns[]` (the ask fails closed)."""


def validate_scope(
    pattern: str, patterns: list[str] | None, always: list[str] | None
) -> tuple[list[str], list[str]]:
    """Defaults `patterns=[pattern]`, `always=patterns`; enforces `always ⊆ patterns`."""
    checked = list(patterns) if patterns else [pattern]
    persist = list(always) if always is not None else list(checked)
    extra = [a for a in persist if a not in checked]
    if extra:
        raise ScopeError(f"always[] must be a subset of patterns[]; not in patterns: {extra}")
    return checked, persist


def group_key(p: Pending) -> tuple[str, str, str, str]:
    """Approval grouping key (spec §25.10): `(permission, cwd_root, risk_tier, tool)`."""
    return (p.permission, p.cwd or "", p.risk, p.tool)


def grouped(pending: list[Pending]) -> list[dict[str, Any]]:
    """Collapsible approval cards: count + first/last pattern + member request ids."""
    groups: dict[tuple[str, str, str, str], list[Pending]] = {}
    for p in pending:
        groups.setdefault(group_key(p), []).append(p)
    return [
        {
            "permission": key[0],
            "cwd_root": key[1] or None,
            "risk_tier": key[2],
            "tool": key[3],
            "count": len(items),
            "first_pattern": items[0].pattern,
            "last_pattern": items[-1].pattern,
            "request_ids": [i.request_id for i in items],
        }
        for key, items in groups.items()
    ]


Emit = Callable[[str, str, dict[str, Any]], Awaitable[None]]


class ApprovalBroker:
    def __init__(self, emit: Emit, timeout_s: float = 300.0) -> None:
        self._emit = emit
        self.timeout_s = timeout_s
        self._pending: dict[str, Pending] = {}

    def pending(self, session_id: str | None = None) -> list[Pending]:
        return [p for p in self._pending.values() if session_id in (None, p.session_id)]

    async def request(
        self,
        session_id: str,
        tool: str,
        permission: str,
        pattern: str,
        detail: str,
        *,
        patterns: list[str] | None = None,
        always: list[str] | None = None,
        cwd: str | None = None,
        risk: str = "medium",
        one_shot: bool = False,
        editable: bool = False,
        input: dict[str, Any] | None = None,
        justification: str | None = None,
    ) -> ApprovalOutcome:
        try:
            checked, persist = validate_scope(pattern, patterns, always)
        except ScopeError as exc:  # fails closed: never shown to the human
            return ApprovalOutcome(False, None, message=str(exc))
        p = Pending(
            uuid.uuid4().hex,
            session_id,
            tool,
            permission,
            pattern,
            detail,
            asyncio.get_running_loop().create_future(),
            checked,
            persist,
            cwd,
            risk,
            one_shot,
            editable,
            input if editable else None,
            justification,
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
                "one_shot": one_shot,
                "risk": risk,
                **_extras(p),
            },
        )
        try:
            outcome = await asyncio.wait_for(asyncio.shield(p.future), timeout=self.timeout_s)
            outcome.request_id = p.request_id
            return outcome
        except TimeoutError:
            # Silence is never consent (§19.2).
            return ApprovalOutcome(False, None, timed_out=True, request_id=p.request_id)
        finally:
            self._pending.pop(p.request_id, None)

    async def reply(
        self,
        request_id: str,
        reply: Reply,
        message: str | None = None,
        on_always: Callable[[Pending], list[str]] | None = None,
        updated_input: dict[str, Any] | None = None,
    ) -> bool:
        """Resolve a request. `reject` cascades to all pendings of the same session;
        `always` auto-resolves pendings that `on_always` reports as now allowed.
        `updated_input` (allow with edited arguments) needs an `editable` request, is never
        sticky (an edited call is approved once) and raises `EditRefused` otherwise."""
        p = self._pending.get(request_id)
        if p is None or p.future.done():
            return False
        if updated_input is not None and (reply == "reject" or not p.editable):
            raise EditRefused(
                "edited input needs an allow reply (once/always)"
                if p.editable
                else "this request does not accept edited input"
            )
        if reply == "always" and (p.one_shot or updated_input is not None):
            reply = "once"  # single-use requests (and edited calls) never persist a grant
        p.future.set_result(ApprovalOutcome(reply != "reject", reply, message, updated_input=updated_input))
        replied: dict[str, Any] = {"request_id": request_id, "reply": reply}
        if updated_input is not None:
            replied["edited"] = True
        await self._emit(p.session_id, "permission.replied", replied)
        if reply == "reject":
            for other in self.pending(p.session_id):
                if other.request_id != request_id and not other.future.done():
                    other.future.set_result(ApprovalOutcome(False, "reject", None))
                    await self._emit(
                        other.session_id,
                        "permission.replied",
                        {"request_id": other.request_id, "reply": "reject", "cascade": True},
                    )
        elif reply == "always" and on_always is not None and not p.one_shot:
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

    async def cascade(self, request_id: str) -> list[str]:
        """Approve (`once`) every other pending request in the same approval group as
        `request_id` (spec §17 bulk approve; group = §25.10 key). Returns the approved ids.
        One-shot requests (ssh/sftp hosts, `net_direct`, sandbox escalation) are decided one
        at a time: they never cascade, in either direction (the group key spans sessions and
        hosts)."""
        p = self._pending.get(request_id)
        if p is None or p.one_shot:
            return []
        key = group_key(p)
        done: list[str] = []
        for other in list(self._pending.values()):
            if (
                other.request_id == request_id
                or other.future.done()
                or other.one_shot
                or group_key(other) != key
            ):
                continue
            other.future.set_result(ApprovalOutcome(True, "once"))
            await self._emit(
                other.session_id,
                "permission.replied",
                {"request_id": other.request_id, "reply": "once", "cascade": True, "via": request_id},
            )
            done.append(other.request_id)
        return done


EngineLookup = Callable[[str], "PermissionEngine | None"]


async def reply_with_grants(
    broker: ApprovalBroker,
    engine_for: EngineLookup,
    request_id: str,
    reply: Reply,
    message: str | None = None,
    cascade: bool = False,
    updated_input: dict[str, Any] | None = None,
) -> bool:
    """Shared by REST and WS: resolve a request; on `always`, persist the grant in the
    session's permission engine and auto-resolve pendings it now allows. `cascade` with a
    `once` reply also approves the rest of the request's approval group. `updated_input`
    (I5) answers `allow` with edited arguments; it never cascades or persists a grant."""
    if updated_input is not None:
        cascade = False
        reply = "once" if reply == "always" else reply
    if cascade and reply == "once" and any(p.request_id == request_id for p in broker.pending()):
        await broker.cascade(request_id)

    def grant_and_scan(granted: Pending) -> list[str]:
        engine = engine_for(granted.session_id)
        if engine is None:
            return []
        for pattern in granted.always or [granted.pattern]:
            engine.grant_always(granted.permission, pattern)
        return [
            p.request_id
            for p in broker.pending(granted.session_id)
            if engine.evaluate(p.permission, p.pattern).action == "allow"
        ]

    return await broker.reply(
        request_id,
        reply,
        message,
        on_always=grant_and_scan if reply == "always" else None,
        updated_input=updated_input,
    )


async def reply_frame(services: Any, request_id: str, r: Any) -> tuple[bool, str | None]:
    """A WS `control_response{permission}` (or REST body): `(ok, error detail)`."""
    try:
        ok = await reply_with_grants(
            services.approvals,
            services.coordinator.engine_for,
            request_id,
            r.reply,
            r.message,
            r.cascade,
            updated_input=getattr(r, "updated_input", None),
        )
    except EditRefused as exc:
        return False, str(exc)
    return ok, None if ok else "no pending request with that id"


def _extras(p: Pending) -> dict[str, Any]:
    """Optional card fields (I5): only present when set, so older events keep their shape."""
    out: dict[str, Any] = {}
    if p.editable:
        out["editable"] = True
        out["input"] = p.input
    if p.justification:
        out["justification"] = p.justification
    return out


def pending_dict(p: Pending) -> dict[str, Any]:
    """The REST `PendingPermission` shape, as a plain dict (WS `connected` frame)."""
    return {
        "request_id": p.request_id,
        "session_id": p.session_id,
        "tool": p.tool,
        "permission": p.permission,
        "pattern": p.pattern,
        "detail": p.detail,
        "one_shot": p.one_shot,
        "risk": p.risk,
        **_extras(p),
    }


# ------------------------------------------------------------------ unattended (§19.9)
UNATTENDED_KINDS: tuple[str, ...] = ("single_query", "cron", "platform")


async def unattended_kind(services: Any, session_id: str, hint: object = None) -> str:
    """Classify a non-interactive session: `cron` (job/webhook delivery session),
    `platform` (messaging-connector session), else `single_query` (`auoreo run`, MCP, ACP).
    `hint` (e.g. `ctx.services["unattended"]`) wins when it names a kind."""
    if isinstance(hint, str) and hint in UNATTENDED_KINDS:
        return hint
    try:
        from sqlalchemy import select

        from auoreo.core.db import DeliveryRow

        async with services.db.session() as s:
            q = select(DeliveryRow.id).where(
                DeliveryRow.session_id == session_id, DeliveryRow.kind.in_(("job", "webhook"))
            )
            if (await s.execute(q.limit(1))).first() is not None:
                return "cron"
    except Exception:  # no deliveries table (minimal test services) → not a cron session
        pass
    import json

    home = getattr(getattr(services, "settings", None), "home", None)
    for path in sorted(home.glob("plugins/*/sessions.json")) if home is not None else []:
        try:
            mapping = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(mapping, dict) and session_id in mapping.values():
            return "platform"
    return "single_query"


def unattended_action(settings: Any, kind: str) -> str:
    """`approve` | `deny` from `settings.approvals.unattended.<kind>` (default deny)."""
    policy = getattr(getattr(settings, "approvals", None), "unattended", None)
    action = getattr(policy, kind, "deny")
    return action if action in ("approve", "deny") else "deny"


def unattended_hint(kind: str) -> str:
    return f"`auoreo config set approvals.unattended.{kind} approve`"
