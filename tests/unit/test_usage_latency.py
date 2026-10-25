"""Per-call wall time + outcome on main-turn usage rows (W1-C)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from auoreo.core.services import Services
from auoreo.core.usage import UsageLedger
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import TextDelta, Usage


async def _turn(svc: Services, text: str) -> tuple[str, str]:
    sess = await svc.create_session()
    rid = await svc.coordinator.submit(sess.id, text, interactive=False)
    await svc.coordinator.wait(sess.id, 10)
    result = svc.coordinator.result(rid or "")
    assert result is not None
    return sess.id, result.status


async def test_main_turn_rows_carry_latency_and_ok(services: Services, gateway: FakeGateway) -> None:
    gateway.queue(Scripted(tool_calls=[("glob", {"pattern": "*"})]), Scripted(text="done"))
    sid, status = await _turn(services, "look")
    assert status == "completed"
    rows = await services.coordinator.runner.usage.by_session(sid)
    assert len(rows) == 2
    assert all(r.outcome == "ok" and r.latency_ms is not None and r.latency_ms >= 0 for r in rows)


async def test_failed_call_writes_zero_token_row_with_error_code(
    services: Services, gateway: FakeGateway
) -> None:
    gateway.queue(Scripted(error=GatewayError("AUTH", "bad key")))
    sid, status = await _turn(services, "hi")
    assert status == "failed"
    rows = await services.coordinator.runner.usage.by_session(sid)
    assert len(rows) == 1
    row = rows[0]
    assert (row.prompt_tokens, row.completion_tokens, row.outcome) == (0, 0, "AUTH")
    assert row.latency_ms is not None


async def test_record_is_backwards_compatible(services: Services) -> None:
    ledger = UsageLedger(services.db)
    row = await ledger.record("s1", "r1", "m", Usage(1, 2, 0.0))
    assert row.latency_ms is None and row.outcome is None


async def test_meter_times_the_call_and_records_once(services: Services) -> None:
    ledger = UsageLedger(services.db)
    meter = ledger.meter("s2", "r2", "m")

    async def stream() -> AsyncIterator[object]:
        yield TextDelta("x")
        yield Usage(3, 4, 0.0)
        raise GatewayError("SERVER", "cut after usage")

    with pytest.raises(GatewayError):
        async for item in meter.wrap(stream()):
            if isinstance(item, Usage):
                await meter.record(item)
    rows = await ledger.by_run("s2", "r2")
    assert [(r.prompt_tokens, r.outcome) for r in rows] == [(3, "ok")]  # no extra error row
