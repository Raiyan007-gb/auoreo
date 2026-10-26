"""Smart permission mode (spec §19.3, §25.10): one cheap aux call decides an `ask`.

`guardian_verdict()` sends the policy (`prompts/guardian.md`, `role: guardian`, never part
of the agent prompt) plus the comment-stripped command wrapped in `<command>…</command>`
to the `small` routing group (or the session model), temperature 0, 16 tokens, 15 s
(`AUOREO_SMART_TIMEOUT` / `i9.guardian.timeout_s`).
The reply must be exactly `APPROVE`, `DENY` or `ESCALATE`; anything else, a timeout or a
gateway error escalates to the human. Verdicts are cached by `sha256(permission+pattern)`
(APPROVE 10 min, DENY 60 s). After 3 consecutive DENYs in a session the breaker trips
(`permission.guardian_breaker`): the running turn is hard-stopped with the typed error
`GUARDIAN_BREAKER` (`runtime/guards.py` consumes `take_breaker_stop`; until then every ask in
that session is denied without a model call), and afterwards the guardian is no longer
consulted for that session — every ask goes to the human.
The executor also consults it for an exec-policy `review` in any mode (`core/policy.py`).
Each call's tokens/cost are recorded in the usage ledger with `run_id="guardian"`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from auoreo.gateway.auxiliary import resolve_aux_model
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import LLMRequest, TextDelta, Usage

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

Verdict = Literal["APPROVE", "DENY", "ESCALATE"]
VERDICTS: frozenset[str] = frozenset({"APPROVE", "DENY", "ESCALATE"})
TIMEOUT_S = 15.0
MAX_TOKENS = 16
PROMPT_CAP = 4000
TTL_S: dict[str, float] = {"APPROVE": 600.0, "DENY": 60.0}
BREAKER_DENIES = 3


@dataclass
class GuardianState:
    """Per-process verdict cache and per-session breaker (kept in `services.ext["guardian"]`)."""

    cache: dict[str, tuple[Verdict, float]] = field(default_factory=dict)
    denies: dict[str, int] = field(default_factory=dict)
    tripped: set[str] = field(default_factory=set)
    stop: set[str] = field(default_factory=set)
    """Sessions whose breaker tripped and whose running turn still has to be hard-stopped."""

    def key(self, permission: str, pattern: str) -> str:
        return hashlib.sha256(f"{permission}\0{pattern}".encode()).hexdigest()

    def cached(self, key: str) -> Verdict | None:
        hit = self.cache.get(key)
        if hit is None:
            return None
        if hit[1] < time.monotonic():
            self.cache.pop(key, None)
            return None
        return hit[0]

    def store(self, key: str, verdict: Verdict) -> None:
        if verdict in TTL_S:
            self.cache[key] = (verdict, time.monotonic() + TTL_S[verdict])


def state_of(services: Services) -> GuardianState:
    st = services.ext.get("guardian")
    if not isinstance(st, GuardianState):
        st = services.ext["guardian"] = GuardianState()
    return st


def take_breaker_stop(services: Services, session_id: str) -> bool:
    """True once after the breaker tripped for `session_id` (the turn must hard-stop)."""
    st = state_of(services)
    if session_id in st.stop:
        st.stop.discard(session_id)
        return True
    return False


def trip(services: Services, session_id: str) -> None:
    """Stop consulting the guardian for this session (asks go to the human)."""
    state_of(services).tripped.add(session_id)


def strip_comments(command: str) -> str:
    lines = [ln for ln in command.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(lines).strip()


def parse_verdict(text: str) -> Verdict:
    token = text.strip()
    return token if token in VERDICTS else "ESCALATE"  # type: ignore[return-value]


def timeout_s(services: Services) -> float:
    """I9: `AUOREO_SMART_TIMEOUT` → `i9.guardian.timeout_s` → 15 s (spec §25.10 L1015)."""
    st = getattr(services, "settings", None)
    override = getattr(st, "smart_timeout", None)
    if override:
        return float(override)
    i9 = getattr(st, "i9", None)
    return float(i9.guardian.timeout_s) if i9 is not None else TIMEOUT_S


def _model(services: Services, model: str | None) -> str:
    return resolve_aux_model(services.settings, "guardian", gateway=services.gateway, main=model)


async def _ask(services: Services, request: LLMRequest) -> str:
    parts: list[str] = []
    async for item in services.gateway.stream(request):
        if isinstance(item, TextDelta):
            parts.append(item.text)
        elif isinstance(item, Usage):
            await _record_spend(services, request, item)
    return "".join(parts)


async def _record_spend(services: Services, request: LLMRequest, usage: Usage) -> None:
    """Guardian spend goes to the usage ledger as `run_id="guardian"` (spec §19.3
    `guardian_spend`), against the session it reviewed. Never fails the verdict."""
    runner = getattr(getattr(services, "coordinator", None), "runner", None)
    ledger = getattr(runner, "usage", None)
    if ledger is None:
        return
    session_id = str(request.metadata.get("session_id") or "guardian")
    try:
        await ledger.record(session_id, "guardian", request.model, usage)
    except Exception:
        log.warning("could not record guardian spend", exc_info=True)


async def guardian_verdict(
    services: Services,
    *,
    tool: str,
    permission: str,
    pattern: str,
    detail: str = "",
    session_id: str | None = None,
    model: str | None = None,
) -> Verdict:
    """One guardian call (or a cache hit). Never raises; failures escalate."""
    st = state_of(services)
    if session_id is not None and session_id in st.stop:
        return "DENY"  # breaker tripped in this turn: no silent escalation before the hard-stop
    if session_id is not None and session_id in st.tripped:
        return "ESCALATE"
    key = st.key(permission, pattern)
    verdict = st.cached(key)
    cache_hit = verdict is not None
    if verdict is None:
        verdict = await _call(services, tool, permission, pattern, session_id, model)
        st.store(key, verdict)
    if session_id is not None:
        await _count(services, st, session_id, verdict)
    await services.audit.record(
        actor="guardian",
        action="permission.guardian",
        session_id=session_id,
        decision=verdict,
        reason="guardian",
        detail={"tool": tool, "permission": permission, "pattern": pattern[:500], "cache_hit": cache_hit},
    )
    return verdict


async def _count(services: Services, st: GuardianState, session_id: str, verdict: Verdict) -> None:
    if verdict == "APPROVE":
        st.denies[session_id] = 0
        return
    if verdict != "DENY":
        return
    n = st.denies[session_id] = st.denies.get(session_id, 0) + 1
    if n >= BREAKER_DENIES and session_id not in st.tripped:
        st.tripped.add(session_id)
        st.stop.add(session_id)
        await services.emit(session_id, "permission.guardian_breaker", {"denies": n})


async def _call(
    services: Services,
    tool: str,
    permission: str,
    pattern: str,
    session_id: str | None,
    model: str | None,
) -> Verdict:
    prompt = services.harness.snapshot.prompts.get("guardian")
    if prompt is None or prompt.section.role != "guardian":
        log.warning("prompts/guardian.md (role: guardian) is missing; escalating")
        return "ESCALATE"
    command = strip_comments(pattern)[:PROMPT_CAP]
    request = LLMRequest(
        model=_model(services, model),
        messages=(
            {"role": "system", "content": prompt.template},
            {
                "role": "user",
                "content": f"Tool: {tool}\nPermission: {permission}\n<command>\n{command}\n</command>",
            },
        ),
        tool_choice="none",
        temperature=0,
        max_tokens=MAX_TOKENS,
        metadata={"purpose": "guardian", **({"session_id": session_id} if session_id else {})},
    )
    budget = timeout_s(services)
    try:
        text = await asyncio.wait_for(_ask(services, request), budget)
    except TimeoutError:
        log.warning("guardian timed out after %ss; escalating", budget)
        return "ESCALATE"
    except GatewayError as exc:
        log.warning("guardian call failed (%s); escalating", exc.code)
        return "ESCALATE"
    return parse_verdict(text)
