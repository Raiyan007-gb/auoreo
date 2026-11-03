"""Deliveries outbox: enqueue/dedupe, claim-at-most-once, crash recovery, worker, providers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy import select

from auoreo.automation.deliveries import DeliveryWorker, Outbox
from auoreo.backends import delivery as providers
from auoreo.backends.delivery import DeliveryProvider, DeliveryReceipt
from auoreo.core.db import AuditRow, EventRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway


@pytest.fixture
def settings(home: Path) -> Settings:
    # the background worker stays asleep: tests drive claim/process themselves
    return Settings(home=home, gateway="fake", approval_timeout_s=5, automation_start_delay_s=3600)


@pytest.fixture
async def services(settings: Settings, workdir: Path, gateway: FakeGateway) -> AsyncIterator[Services]:
    svc = await Services.create(settings, cwd=workdir, gateway=gateway, with_mcp=False, with_memory=False)
    try:
        yield svc
    finally:
        await svc.close()


async def server_events(svc: Services, prefix: str) -> list[EventRow]:
    async with svc.db.session() as s:
        q = select(EventRow).where(EventRow.aggregate_id == "server").order_by(EventRow.seq)
        return [e for e in (await s.execute(q)).scalars() if e.type.startswith(prefix)]


async def test_enqueue_dedupes_on_kind_source_external_id(services: Services) -> None:
    ob = Outbox(services)
    first = await ob.enqueue("webhook", "gh", "abc", {"prompt": "x"})
    assert first is not None and first.status == "pending"
    assert await ob.enqueue("webhook", "gh", "abc", {"prompt": "y"}) is None
    assert await ob.enqueue("webhook", "other", "abc", {"prompt": "y"}) is not None
    assert await ob.exists("webhook", "gh", "abc")
    assert [e.type for e in await server_events(services, "delivery.")] == ["delivery.queued"] * 2


async def test_two_concurrent_claims_have_one_winner(services: Services) -> None:
    ob = Outbox(services)
    row = await ob.enqueue("job", "j", "run-1", {"prompt": "x"})
    assert row is not None
    a, b = await asyncio.gather(ob.claim_next(), ob.claim_next())
    winners = [r for r in (a, b) if r is not None]
    assert len(winners) == 1 and winners[0].id == row.id
    assert winners[0].status == "delivering" and winners[0].attempts == 1 and winners[0].claimed_at
    assert await ob.claim(row.id) is None  # never claimed twice
    assert await ob.claim_next() is None


async def test_claims_spread_over_distinct_rows(services: Services) -> None:
    ob = Outbox(services)
    for i in range(4):
        await ob.enqueue("job", "j", f"run-{i}", {"prompt": "x"})
    got = await asyncio.gather(*(ob.claim_next() for _ in range(6)))
    ids = [r.id for r in got if r is not None]
    assert len(ids) == 4 and len(set(ids)) == 4


async def test_delivering_rows_become_unknown_on_restart(settings: Settings, workdir: Path) -> None:
    svc = await Services.create(
        settings, cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False
    )
    ob = Outbox(svc)
    await ob.enqueue("webhook", "gh", "crash", {"prompt": "x"})
    keep = await ob.enqueue("webhook", "gh", "later", {"prompt": "x"})
    claimed = await ob.claim_next()
    assert claimed is not None and claimed.external_id == "crash"
    await svc.close()  # "crash" while delivering

    svc2 = await Services.create(
        settings, cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False
    )
    try:
        ob2 = Outbox(svc2)
        crashed = await ob2.get(claimed.id)
        assert crashed is not None and crashed.status == "unknown" and "not retried" in (crashed.error or "")
        assert keep is not None and (await ob2.get(keep.id)).status == "pending"  # type: ignore[union-attr]
        async with svc2.db.session() as s:
            audits = list(
                (await s.execute(select(AuditRow).where(AuditRow.action == "delivery.unknown"))).scalars()
            )
        assert len(audits) == 1 and audits[0].detail["delivery_id"] == claimed.id
        # unknown rows are never claimed again
        nxt = await ob2.claim_next()
        assert nxt is not None and nxt.id == keep.id
    finally:
        await svc2.close()


async def test_worker_runs_agent_and_logs_delivery(services: Services, home: Path) -> None:
    ob = Outbox(services)
    row = await ob.enqueue("job", "nightly", "run-7", {"prompt": "hello there", "deliver": ["log"]})
    assert row is not None
    worker = DeliveryWorker(services)
    assert await worker.run_once() is True
    done = await ob.get(row.id)
    assert done is not None and done.status == "delivered", done.error
    assert done.result["text"] == "echo: hello there" and done.result["provider"] == "log"
    assert done.session_id and done.session_id.startswith("ses_")
    line = json.loads((home / "deliveries.log").read_text().splitlines()[-1])
    assert line["source"] == "nightly" and line["text"] == "echo: hello there"
    kinds = [e.type for e in await server_events(services, "delivery.")]
    assert kinds == ["delivery.queued", "delivery.sent", "delivery.delivered"]
    ev = (await server_events(services, "delivery.delivered"))[0]
    assert ev.data["session_id"] == done.session_id and ev.data["external_id"] == "run-7"


async def test_providers_are_tried_in_order_until_one_succeeds(services: Services) -> None:
    class Flaky(DeliveryProvider):
        async def send(self, target: str, text: str, *, meta: dict[str, Any]) -> DeliveryReceipt:
            return DeliveryReceipt(False, "down")

    providers.register("flaky", Flaky())
    try:
        ob = Outbox(services)
        row = await ob.enqueue("job", "j", "r", {"prompt": "hi", "deliver": ["flaky", "nope", "log"]})
        assert row is not None
        claimed = await ob.claim(row.id)
        assert claimed is not None
        done = await DeliveryWorker(services).process(claimed)
        assert done.status == "delivered"
        assert [a["provider"] for a in done.result["attempts"]] == ["flaky", "nope", "log"]
        assert [a["ok"] for a in done.result["attempts"]] == [False, False, True]
    finally:
        providers.unregister("flaky")


async def test_all_providers_failing_marks_the_row_failed(services: Services) -> None:
    ob = Outbox(services)
    row = await ob.enqueue("webhook", "gh", "x", {"prompt": "hi", "deliver": ["nope"]})
    assert row is not None
    claimed = await ob.claim(row.id)
    assert claimed is not None
    done = await DeliveryWorker(services).process(claimed)
    assert done.status == "failed" and "unknown delivery provider" in (done.error or "")
    failed = await server_events(services, "delivery.failed")
    assert failed and failed[0].data["source"] == "gh"


async def test_unknown_agent_fails_without_crashing(services: Services) -> None:
    ob = Outbox(services)
    row = await ob.enqueue("job", "j", "bad", {"prompt": "hi", "agent": "ghost"})
    assert row is not None
    claimed = await ob.claim(row.id)
    assert claimed is not None
    done = await DeliveryWorker(services).process(claimed)
    assert done.status == "failed" and "ghost" in (done.error or "")


async def test_background_worker_drains_the_queue(settings: Settings, workdir: Path) -> None:
    st = settings.model_copy(update={"automation_start_delay_s": 0.0, "automation_poll_s": 0.2})
    svc = await Services.create(st, cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False)
    try:
        ob = Outbox(svc)
        row = await ob.enqueue("job", "j", "bg", {"prompt": "background"})
        assert row is not None
        done = await ob.wait_terminal(row.id, 10)
        assert done is not None and done.status == "delivered"
    finally:
        await svc.close()


# ------------------------------------------------------------------ retention


async def test_prune_keeps_only_the_newest_terminal_rows(services: Services) -> None:
    ob = Outbox(services)
    rows = [await ob.enqueue("webhook", "gh", f"d{i}", {"prompt": "x"}) for i in range(5)]
    assert all(r is not None for r in rows)
    for r in rows[:4]:  # d0..d3 reach a terminal state (d1 fails); d4 stays pending
        assert r is not None and await ob.claim(r.id) is not None
        await ob.finish(r.id, ok=r.external_id != "d1", error="boom" if r.external_id == "d1" else None)
    assert await ob.prune(keep=2) == 2  # d0 and d1 go; the newest two terminal rows and the pending one stay
    assert sorted(r.external_id for r in await ob.list(limit=10)) == ["d2", "d3", "d4"]
    assert not await ob.exists("webhook", "gh", "d0")  # a re-sent d0 is accepted again
    assert await ob.prune(keep=2) == 0
    assert await ob.prune(keep=0) == 0  # 0 = keep everything


async def test_worker_prunes_after_each_delivery(settings: Settings, workdir: Path) -> None:
    st = settings.model_copy(update={"automation_deliveries_keep": 1})
    svc = await Services.create(st, cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False)
    try:
        ob = Outbox(svc)
        for i in range(3):
            await ob.enqueue("job", "j", f"r{i}", {"prompt": "hi", "deliver": ["log"]})
        worker = DeliveryWorker(svc)
        while await worker.run_once():
            pass
        assert [r.external_id for r in await ob.list()] == ["r2"]
    finally:
        await svc.close()


async def test_dynamic_provider_wins_over_folder_backend(services: Services) -> None:
    class Mine(DeliveryProvider):
        async def send(self, target: str, text: str, *, meta: dict[str, Any]) -> DeliveryReceipt:
            return DeliveryReceipt(True, "mine")

    assert type(providers.resolve(services, "log")).__name__ == "Backend"
    mine = Mine()
    providers.register("log", mine)
    try:
        assert providers.resolve(services, "log") is mine and mine.services is services
    finally:
        providers.unregister("log")
    assert providers.resolve(services, "log") is not mine
    with pytest.raises(KeyError, match="unknown delivery provider"):
        providers.resolve(services, "carrier_pigeon")


# ------------------------------------------------------------------ github_comment


async def test_github_comment_posts_with_the_stored_token(services: Services, httpx_mock: HTTPXMock) -> None:
    gh = providers.resolve(services, "github_comment")
    status = await gh.check()
    assert not status.ok and "github/token" in status.detail
    assert not (await gh.send("o/r#5", "hi", meta={})).ok  # no token yet
    services.secrets.set("github/token", "ghp_testtoken1234567890")
    assert (await gh.check()).ok
    httpx_mock.add_response(
        method="POST",
        url="https://api.github.com/repos/octo/repo/issues/42/comments",
        status_code=201,
        json={"id": 9001, "html_url": "https://github.com/octo/repo/pull/42#issuecomment-9001"},
    )
    receipt = await gh.send("octo/repo#42", "LGTM", meta={"kind": "webhook"})
    assert receipt.ok and receipt.external_id == "9001" and receipt.detail.endswith("9001")
    req = httpx_mock.get_requests()[0]
    assert req.headers["Authorization"] == "Bearer ghp_testtoken1234567890"
    assert json.loads(req.content) == {"body": "LGTM"}


async def test_github_comment_rejects_bad_targets_and_errors(
    services: Services, httpx_mock: HTTPXMock
) -> None:
    services.secrets.set("github/token", "ghp_testtoken1234567890")
    gh = providers.resolve(services, "github_comment")
    assert "owner/repo#number" in (await gh.send("not-a-target", "x", meta={})).detail
    httpx_mock.add_response(method="POST", status_code=403, json={"message": "nope"})
    receipt = await gh.send("o/r#1", "x", meta={})
    assert not receipt.ok and "403" in receipt.detail
