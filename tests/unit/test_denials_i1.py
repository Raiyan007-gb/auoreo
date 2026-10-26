"""I1 §19.5/§19.3: the denial tally is per session (20 total → hard-stop + fall back to
prompting) and 3 consecutive guardian DENYs hard-stop the turn with `GUARDIAN_BREAKER`."""

from __future__ import annotations

import pytest

from auoreo.core.guardian import state_of
from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.runtime import guards
from auoreo.runtime.guards import tally_of

from .test_loop_recovery import final


async def _turn(svc: Services, sid: str, text: str) -> tuple[str, dict]:  # type: ignore[type-arg]
    sub = svc.bus.subscribe(frozenset({f"session:{sid}"}))
    rid = await svc.coordinator.submit(sid, text, interactive=False)
    await svc.coordinator.wait(sid, 15)
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    svc.bus.unsubscribe(sub)
    result = svc.coordinator.result(rid or "")
    assert result is not None
    return result.status, final(events).data


def _denied_call(i: int) -> Scripted:
    return Scripted(tool_calls=[("bash", {"command": f"rm -rf /tmp/i1-{i}"})])


async def test_denial_tally_spans_turns_and_caps_the_session(
    services: Services, gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    services.settings.permission_mode = "manual"
    monkeypatch.setattr(guards, "SESSION_DENIAL_LIMIT", 4)
    sid = (await services.create_session()).id
    # turn 1: two denials then a reply → consecutive 2, total 2 (survives the turn)
    gateway.queue(_denied_call(1), _denied_call(2), Scripted(text="ok"), Scripted(text="title"))
    status, _ = await _turn(services, sid, "one")
    assert status == "completed"
    t = tally_of(services, sid)
    assert (t.consecutive, t.total) == (2, 2)
    # turn 2: one more denial completes 3 consecutive across turns → DENIAL_LOOP
    gateway.queue(_denied_call(3))  # blocked: nothing else is consumed
    status, data = await _turn(services, sid, "two")
    assert status == "blocked" and data["error"]["code"] == "DENIAL_LOOP"
    # turn 3: the 4th denial in this session hits the (patched) session cap
    gateway.queue(_denied_call(4))
    status, data = await _turn(services, sid, "three")
    assert status == "blocked" and data["error"]["code"] == "DENIAL_LIMIT"
    assert sid in state_of(services).tripped  # smart mode now always prompts the human
    assert (tally_of(services, sid).consecutive, tally_of(services, sid).total) == (0, 0)


async def test_three_guardian_denies_hard_stop_the_turn(services: Services, gateway: FakeGateway) -> None:
    services.settings.permission_mode = "smart"
    gateway.guardian_script.extend(["DENY", "DENY", "DENY"])
    sid = (await services.create_session()).id
    gateway.queue(
        Scripted(
            tool_calls=[
                ("bash", {"command": "rm -rf /tmp/g1"}),
                ("bash", {"command": "rm -rf /tmp/g2"}),
                ("bash", {"command": "rm -rf /tmp/g3"}),
            ]
        ),
        Scripted(text="never"),
    )
    status, data = await _turn(services, sid, "go")
    assert status == "blocked" and data["error"]["code"] == "GUARDIAN_BREAKER"
    assert sid not in state_of(services).stop  # consumed by the guards
