"""W1-E multi-worker opt-in: refuse without redis_url; Redis bridge fan-out (fakeredis)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.core import diagnostics, extensions
from auoreo.core.bus import EventBus
from auoreo.core.bus_redis import REFUSE_MSG, RedisBridge
from auoreo.core.crypto_columns import ColumnCipher, DbKeyring
from auoreo.core.settings import Settings
from auoreo.schema.events import ServerEvent

fakeredis = pytest.importorskip("fakeredis")


def _ev(seq: int, text: str = "x") -> ServerEvent:
    return ServerEvent(
        id=str(seq),
        seq=seq,
        type="note",
        aggregate_id="s1",
        aggregate_seq=seq,
        data={"text": text},
        created_at=datetime.now(UTC),
    )


def test_serve_workers_refuses_without_redis_url() -> None:
    r = CliRunner().invoke(app, ["serve", "--workers", "2", "--port", "0"])
    assert r.exit_code == 2
    assert "redis_url" in r.output and "sticky" in r.output


def test_starter_is_registered_once() -> None:
    assert extensions.STARTERS.count("auoreo.core.bus_redis:start") == 1


async def test_doctor_workers_row(home: object) -> None:
    rows = {c.check: c for c in await diagnostics.run_checks(Settings(gateway="fake", workers=3))}
    assert rows["workers"].level == "error" and rows["workers"].remedy == REFUSE_MSG
    rows = {c.check: c for c in await diagnostics.run_checks(Settings(gateway="fake"))}
    assert rows["workers"].level == "ok"


async def _drain(q: asyncio.Queue[ServerEvent], wait: float = 0.3) -> list[ServerEvent]:
    out: list[ServerEvent] = []
    while True:
        try:
            out.append(await asyncio.wait_for(q.get(), wait))
        except TimeoutError:
            return out


@pytest.mark.parametrize("encrypt", [False, True])
async def test_bridge_fans_out_without_echo_or_dupes(encrypt: bool, tmp_path: object) -> None:
    server = fakeredis.FakeServer()
    cipher = ColumnCipher("columns" if encrypt else "off", DbKeyring(tmp_path))  # type: ignore[arg-type]
    bus_a, bus_b = EventBus(), EventBus()
    a = RedisBridge(bus_a, fakeredis.FakeAsyncRedis(server=server), cipher=cipher, worker_id="A")
    b = RedisBridge(bus_b, fakeredis.FakeAsyncRedis(server=server), cipher=cipher, worker_id="B")
    await a.start()
    await b.start()
    sub_a, sub_b = bus_a.subscribe(), bus_b.subscribe()
    try:
        bus_a.publish(_ev(1, "from-a"))
        bus_b.publish(_ev(2, "from-b"))
        got_a = await _drain(sub_a.queue)
        got_b = await _drain(sub_b.queue)
        assert [(e.seq, e.data["text"]) for e in got_a] == [(1, "from-a"), (2, "from-b")]
        assert sorted((e.seq, e.data["text"]) for e in got_b) == [(1, "from-a"), (2, "from-b")]
        # a duplicate on the wire (e.g. a re-publish) is delivered once
        await fakeredis.FakeAsyncRedis(server=server).publish("auoreo:events", b._encode(_ev(2, "dup")))
        await fakeredis.FakeAsyncRedis(server=server).publish(
            "auoreo:events", RedisBridge(EventBus(), None, cipher=cipher, worker_id="C")._encode(_ev(3))
        )
        late_a = await _drain(sub_a.queue)
        assert [e.seq for e in late_a] == [3]  # 2 was already seen; 3 is new
        assert a.forwarded == 1 and b.forwarded == 1  # remote events are never re-forwarded
    finally:
        await a.close()
        await b.close()
