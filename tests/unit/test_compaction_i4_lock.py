"""I4 §11.9 S4(d)/S6: manual `/compact` requires idle and holds the start lock."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.runtime.coordinator import SessionBusy
from tests.unit.test_compaction_g2b import since, turns


async def test_compact_refused_while_a_turn_runs(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 12)
    gateway.queue(Scripted(text="slow", delay_s=0.5))
    mark = await services.events.last_seq()
    await services.coordinator.submit(sess.id, "a slow turn", interactive=False)
    await asyncio.sleep(0.05)
    assert services.coordinator.is_busy(sess.id)
    assert await services.coordinator.submit(sess.id, "/compact", interactive=False) is None
    with pytest.raises(SessionBusy):
        async with services.coordinator.compaction_lock(sess.id):
            pass
    await services.coordinator.wait(sess.id, 10)
    events = await since(services, sess.id, mark)
    skipped = [e for e in events if e.type == "compaction.skipped"]
    assert skipped and skipped[0].data["reason"].startswith("busy")
    assert not [e for e in events if e.type == "compaction.started"]


async def test_input_during_manual_compaction_waits_for_commit(
    services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 15)
    gateway.compaction_script.append(Scripted(text="## Objective\n- keep going", delay_s=0.4))
    mark = await services.events.last_seq()
    task = asyncio.create_task(services.coordinator.submit(sess.id, "/compact", interactive=False))
    await asyncio.sleep(0.1)
    assert services.coordinator.is_busy(sess.id)
    run_id = await services.coordinator.submit(sess.id, "arrives mid-summary", interactive=False)
    assert run_id is not None
    await asyncio.sleep(0.05)
    assert not services.coordinator.is_running(sess.id)  # the turn has not started
    await task
    await services.coordinator.wait(sess.id, 10)
    types = [e.type for e in await since(services, sess.id, mark)]
    assert types.index("compaction.completed") < types.index("run.started")
    assert services.coordinator.result(run_id) is not None


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def test_api_compact_conflicts_while_busy(
    client: httpx.AsyncClient, services: Services, gateway: FakeGateway
) -> None:
    sess = await services.create_session()
    await turns(services, sess.id, 12)
    gateway.queue(Scripted(text="slow", delay_s=0.4))
    await services.coordinator.submit(sess.id, "slow", interactive=False)
    await asyncio.sleep(0.05)
    assert (await client.post(f"/api/sessions/{sess.id}/compact")).status_code == 409
    await services.coordinator.wait(sess.id, 10)
    assert (await client.post(f"/api/sessions/{sess.id}/compact")).status_code == 200
