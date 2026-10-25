"""Scheduler: schedule parsing, firing, catch-up, pause/resume, max_instances, resync, API/CLI."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from apscheduler.triggers.interval import IntervalTrigger
from asgi_lifespan import LifespanManager
from sqlalchemy import select
from typer.testing import CliRunner

from auoreo.api.app import create_app
from auoreo.automation.scheduler import JobScheduler
from auoreo.automation.schedules import ScheduleError, missed_fire, parse_schedule
from auoreo.core.db import DeliveryRow, EventRow, JobRunRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from tests.conftest import write


def job_yaml(name: str, schedule: str = "every 1h", extra: str = "") -> str:
    return (
        f'name: {name}\ndescription: test job {name}\nschedule: "{schedule}"\n'
        f'prompt: "tick {{{{job}}}} at {{{{now}}}}"\ntimeout_s: 30\n{extra}'
    )


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=5, automation_start_delay_s=0)


@pytest.fixture
def jobs_dir(workdir: Path) -> Path:
    d = workdir / ".auoreo" / "jobs"
    write(d / "tick.yaml", job_yaml("tick"))
    write(d / "hourly.yaml", job_yaml("hourly"))
    write(d / "nocatch.yaml", job_yaml("nocatch", extra="catch_up: false\n"))
    write(d / "dormant.yaml", job_yaml("dormant", extra="enabled: false\n"))
    return d


@pytest.fixture
async def services(
    settings: Settings, workdir: Path, gateway: FakeGateway, jobs_dir: Path
) -> AsyncIterator[Services]:
    svc = await Services.create(settings, cwd=workdir, gateway=gateway, with_mcp=False, with_memory=False)
    try:
        yield svc
    finally:
        await svc.close()


def sched_of(svc: Services) -> JobScheduler:
    s = svc.ext["scheduler"]
    assert isinstance(s, JobScheduler) and s.active
    return s


async def runs_of(svc: Services, job: str) -> list[JobRunRow]:
    async with svc.db.session() as s:
        q = select(JobRunRow).where(JobRunRow.job == job).order_by(JobRunRow.id)
        return list((await s.execute(q)).scalars())


async def wait_for(pred: Any, timeout: float = 10) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not await pred():
        assert loop.time() < deadline, "timed out"
        await asyncio.sleep(0.1)


# ------------------------------------------------------------------ parsing


@pytest.mark.parametrize(
    ("expr", "kind", "period"),
    [
        ("0 9 * * 1-5", "cron", None),
        ("*/15 * * * *", "cron", None),
        ("every 30m", "interval", 1800),
        ("every 2h", "interval", 7200),
        ("every 1d", "interval", 86400),
        ("2031-01-01T09:00:00+00:00", "date", None),
        ("2031-01-01T09:00:00", "date", None),
    ],
)
def test_schedule_parsing(expr: str, kind: str, period: float | None) -> None:
    s = parse_schedule(expr, "UTC", "j")
    assert s.kind == kind and s.period_s == period
    assert s.next_fire(datetime(2030, 6, 1, tzinfo=UTC)) is not None


@pytest.mark.parametrize("expr", ["61 * * * *", "every 0m", "every 5 parsecs", "whenever", "0 9 * *"])
def test_invalid_schedules_name_the_job(expr: str) -> None:
    with pytest.raises(ScheduleError, match="job 'broken'"):
        parse_schedule(expr, None, "broken")


def test_unknown_time_zone() -> None:
    with pytest.raises(ScheduleError, match="unknown time zone"):
        parse_schedule("0 9 * * *", "Mars/Olympus", "j")


def test_cron_weekdays_use_crontab_numbering() -> None:
    """APScheduler 3 counts Monday=0; `1-5` must still mean Monday..Friday."""
    s = parse_schedule("0 9 * * 1-5", "Europe/Berlin", "j")
    saturday = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
    nxt = s.next_fire(saturday)
    assert nxt is not None and nxt.weekday() == 0 and nxt.hour == 9  # Monday 09:00 Berlin
    sunday_only = parse_schedule("0 9 * * 0", "UTC", "j").next_fire(saturday)
    assert sunday_only is not None and sunday_only.weekday() == 6


def test_missed_fire_window() -> None:
    hourly = parse_schedule("every 1h", "UTC", "j")
    now = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
    assert missed_fire(hourly, now - timedelta(minutes=65), now) == now - timedelta(minutes=5)
    assert missed_fire(hourly, now - timedelta(minutes=10), now) is None  # not yet due
    assert missed_fire(hourly, None, now) is None  # never ran: nothing to catch up
    daily = parse_schedule("0 9 * * *", "UTC", "j")
    yesterday = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)
    assert missed_fire(daily, yesterday, datetime(2030, 1, 2, 9, 30, tzinfo=UTC)) == datetime(
        2030, 1, 2, 9, 0, tzinfo=UTC
    )
    assert missed_fire(daily, yesterday, datetime(2030, 1, 2, 12, 0, tzinfo=UTC)) is None  # > 2h late


# ------------------------------------------------------------------ firing


async def test_interval_job_fires_and_delivers(services: Services, home: Path) -> None:
    sched = sched_of(services)
    assert set(sched.manifests()) >= {"tick", "hourly", "dormant"}
    assert sched.aps.get_job("dormant") is None and sched.aps.get_job("tick") is not None
    sched.trigger_overrides["tick"] = IntervalTrigger(seconds=1)
    await sched.resync()

    async def done() -> bool:
        return any(r.status == "ok" for r in await runs_of(services, "tick"))

    await wait_for(done)
    run = next(r for r in await runs_of(services, "tick") if r.status == "ok")
    assert run.session_id and run.finished_at and not run.catch_up
    async with services.db.session() as s:
        d = (
            await s.execute(select(DeliveryRow).where(DeliveryRow.external_id == f"run-{run.id}"))
        ).scalar_one()
        types = [
            e.type
            for e in (await s.execute(select(EventRow).where(EventRow.aggregate_id == "server"))).scalars()
        ]
    assert d.kind == "job" and d.status == "delivered" and d.result["text"].startswith("echo: tick tick at ")
    assert {"cron.fired", "cron.delivered", "delivery.delivered"} <= set(types)
    assert "tick" in (home / "deliveries.log").read_text()


async def test_catch_up_fires_once(services: Services) -> None:
    sched = sched_of(services)
    missed = datetime.now(UTC) - timedelta(minutes=65)
    async with services.db.session() as s:
        for job in ("hourly", "nocatch"):
            s.add(JobRunRow(job=job, scheduled_for=missed, status="ok"))
        await s.commit()
    assert await sched.catch_up() == ["hourly"]
    assert await sched.catch_up() == []  # once
    runs = await runs_of(services, "hourly")
    assert len(runs) == 2 and runs[-1].catch_up and runs[-1].status == "ok"
    assert len(await runs_of(services, "nocatch")) == 1


async def test_duplicate_scheduled_for_is_skipped(services: Services) -> None:
    sched = sched_of(services)
    when = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)
    assert await sched.start_run("tick", scheduled_for=when) is not None
    assert await sched.start_run("tick", scheduled_for=when) is None
    with pytest.raises(KeyError):
        await sched.start_run("ghost")


async def test_pause_and_resume(services: Services, home: Path) -> None:
    sched = sched_of(services)
    assert sched.next_run("hourly") is not None
    await sched.set_paused("hourly", True)
    assert sched.aps.get_job("hourly").next_run_time is None and sched.next_run("hourly") is None
    assert json.loads((home / "cron_state.json").read_text()) == {"paused": ["hourly"]}
    await sched.resync()  # pause survives a resync
    assert sched.aps.get_job("hourly").next_run_time is None
    await sched.set_paused("hourly", False)
    assert sched.next_run("hourly") is not None


async def test_max_instances_prevents_overlap(services: Services) -> None:
    sched = sched_of(services)
    active = 0
    peak = 0
    calls = 0

    async def slow(run_id: int) -> Any:
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(2.5)
        active -= 1

    sched.execute = slow  # type: ignore[method-assign]
    sched.trigger_overrides["tick"] = IntervalTrigger(seconds=1)
    await sched.resync()
    await asyncio.sleep(3.6)
    assert calls >= 1 and peak == 1


async def test_resync_after_reload_adds_new_job(services: Services, jobs_dir: Path) -> None:
    sched = sched_of(services)
    assert sched.aps.get_job("fresh") is None
    sched.watch_interval_s = 0.05
    sched._spawn(sched._watch())
    write(jobs_dir / "fresh.yaml", job_yaml("fresh", "*/5 * * * *"))
    services.harness.reload()

    async def registered() -> bool:
        return sched.aps.get_job("fresh") is not None

    await wait_for(registered, 5)
    (jobs_dir / "fresh.yaml").unlink()
    services.harness.reload()
    await sched.resync()
    assert sched.aps.get_job("fresh") is None


async def test_bad_schedule_is_reported_not_fatal(services: Services, jobs_dir: Path) -> None:
    sched = sched_of(services)
    write(jobs_dir / "weird.yaml", job_yaml("weird", "every fortnight"))
    services.harness.reload()
    await sched.resync()
    assert "weird" in sched.errors and sched.aps.get_job("tick") is not None


# ------------------------------------------------------------------ API + CLI


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


async def test_cron_api(client: httpx.AsyncClient, services: Services) -> None:
    jobs = {j["name"]: j for j in (await client.get("/api/cron")).json()}
    assert jobs["hourly"]["next_run_at"] and jobs["dormant"]["enabled"] is False
    assert jobs["dormant"]["next_run_at"] is None and jobs["tick"]["last_run"] is None
    r = await client.post("/api/cron/tick/run")
    assert r.status_code == 202
    run_id = r.json()["run_id"]

    async def finished() -> bool:
        runs = (await client.get("/api/cron/tick/runs")).json()
        return bool(runs) and runs[0]["id"] == run_id and runs[0]["status"] == "ok"

    await wait_for(finished)
    assert (await client.post("/api/cron/hourly/pause")).json() == {"job": "hourly", "paused": True}
    hourly = next(j for j in (await client.get("/api/cron")).json() if j["name"] == "hourly")
    assert hourly["paused"] is True and hourly["next_run_at"] is None
    assert (await client.post("/api/cron/hourly/resume")).json()["paused"] is False
    assert (await client.post("/api/cron/ghost/run")).status_code == 404


def test_cron_cli_in_process(
    home: Path, workdir: Path, jobs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auoreo.cli.app import app

    monkeypatch.chdir(workdir)
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    runner = CliRunner()
    listed = runner.invoke(app, ["cron", "list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert {j["name"] for j in json.loads(listed.output)} >= {"tick", "hourly"}
    ran = runner.invoke(app, ["cron", "run", "tick"])
    assert ran.exit_code == 0, ran.output
    assert ran.output.startswith("run ") and " ok session=ses_" in ran.output
    runs = runner.invoke(app, ["cron", "runs", "tick", "--json"])
    assert json.loads(runs.output)[0]["status"] == "ok"
    assert runner.invoke(app, ["cron", "pause", "tick"]).exit_code == 0
    assert json.loads((home / "cron_state.json").read_text()) == {"paused": ["tick"]}
    dl = runner.invoke(app, ["deliveries", "list", "--json"])
    assert json.loads(dl.output)[0]["kind"] == "job"
    replay = runner.invoke(app, ["deliveries", "replay", str(json.loads(dl.output)[0]["id"])])
    assert replay.exit_code == 0 and replay.output.startswith("tick tick at ")
