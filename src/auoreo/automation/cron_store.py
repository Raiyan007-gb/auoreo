"""Agent- and API-created jobs (spec §9 `cronjob`, §16.7, §25.11): reminders and cron jobs
persisted as `<home>/jobs/<name>.yaml` (the runtime definition root), so the harness discovers
them like any other job and `auoreo cron` / `GET /api/cron` show them.

- `in 20m` / `in 2h` / `in 1d` (a reminder) becomes an ISO one-shot at write time; the
  original text is kept in the description.
- `dry_run` validates without writing and returns the next 5 fires + the catch-up class.
- Only jobs under `<home>/jobs/` can be removed (bundled/project jobs are read-only here).
- After a write or removal the harness reloads and the scheduler re-syncs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from auoreo.automation.scheduler import scheduler_of
from auoreo.automation.schedules import Schedule, ScheduleError, parse_schedule
from auoreo.harness.manifests import JobManifest

if TYPE_CHECKING:
    from auoreo.core.services import Services

IN_RE = re.compile(r"^in\s+(\d+)\s*(m|min|h|d)$", re.IGNORECASE)
UNIT_S = {"m": 60, "min": 60, "h": 3600, "d": 86_400}
HEADER = "# created by AUOREO (cronjob tool / POST /api/cron); safe to edit or delete\n"
FIRES = 5


class CronStoreError(ValueError):
    """A model/API-visible refusal (invalid job, name taken, not removable)."""


@dataclass
class Plan:
    manifest: JobManifest
    schedule: Schedule
    next_fires: list[datetime]
    catch_up: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.manifest.name,
            "schedule": self.manifest.schedule,
            "kind": self.schedule.kind,
            "tz": str(self.schedule.tz),
            "next_fires": [f.isoformat() for f in self.next_fires],
            "catch_up": self.catch_up,
        }


def normalize(schedule: str, now: datetime | None = None) -> str:
    """`in <n>m|h|d` → ISO one-shot (UTC); anything else unchanged."""
    text = schedule.strip()
    if m := IN_RE.match(text):
        when = (now or datetime.now(UTC)) + timedelta(seconds=int(m.group(1)) * UNIT_S[m.group(2).lower()])
        return when.replace(microsecond=0).isoformat()
    return text


def next_fires(sched: Schedule, now: datetime | None = None, n: int = FIRES) -> list[datetime]:
    out: list[datetime] = []
    prev: datetime | None = None
    cursor = (now or datetime.now(UTC)).astimezone(sched.tz)
    for _ in range(n):
        fire = sched.trigger.get_next_fire_time(prev, cursor)
        if not isinstance(fire, datetime):
            break
        out.append(fire)
        prev, cursor = fire, fire + timedelta(microseconds=1)
    return out


def catch_up_class(m: JobManifest, sched: Schedule) -> str:
    if sched.kind == "date":
        return "one-shot (grace 120s)"
    if not m.catch_up:
        return "no catch-up"
    return f"fire-once within {sched.grace_s():.0f}s of a missed fire"


def plan(data: dict[str, Any], now: datetime | None = None) -> Plan:
    raw = dict(data)
    original = str(raw.get("schedule", ""))
    raw["schedule"] = normalize(original, now)
    if raw["schedule"] != original.strip() and raw.get("description"):
        raw["description"] = f"{raw['description']} ({original.strip()})"
    try:
        manifest = JobManifest.model_validate(raw)
        sched = parse_schedule(manifest.schedule, manifest.tz, manifest.name)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ())) or "job"
        raise CronStoreError(f"invalid job: {where}: {first.get('msg')}") from exc
    except ScheduleError as exc:
        raise CronStoreError(str(exc)) from exc
    if sched.kind == "date" and sched.run_at is not None and sched.run_at <= (now or datetime.now(UTC)):
        raise CronStoreError(f"job '{manifest.name}': one-shot time {manifest.schedule} is in the past")
    return Plan(manifest, sched, next_fires(sched, now), catch_up_class(manifest, sched))


def jobs_dir(services: Services) -> Path:
    return Path(services.settings.home) / "jobs"


def path_of(services: Services, name: str) -> Path:
    return jobs_dir(services) / f"{name}.yaml"


async def _reload(services: Services) -> None:
    diff = services.harness.reload()
    await services.emit("server", "harness.reloaded", diff)
    await scheduler_of(services).resync()


async def create(
    services: Services, data: dict[str, Any], *, dry_run: bool = False, actor: str = "agent"
) -> Plan:
    p = plan(data)
    name = p.manifest.name
    if dry_run:
        return p
    if name in services.harness.jobs:
        raise CronStoreError(f"a job named '{name}' already exists; pick another name or remove it first")
    target = path_of(services, name)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = p.manifest.model_dump(mode="json", exclude_defaults=True)
    body.setdefault("name", name)
    tmp = target.with_suffix(".yaml.tmp")
    tmp.write_text(HEADER + yaml.safe_dump(body, sort_keys=False, allow_unicode=True), encoding="utf-8")
    tmp.replace(target)
    await _reload(services)
    await services.audit.record(
        actor=actor,
        action="cron.created",
        decision="allow",
        detail={"job": name, "schedule": p.manifest.schedule},
    )
    await services.emit("server", "cron.created", {"job": name, **p.as_dict(), "actor": actor})
    return p


def removable(services: Services, name: str) -> Path:
    loaded = services.harness.jobs.get(name)
    if loaded is None:
        raise CronStoreError(f"unknown job '{name}'")
    path = Path(loaded.path).resolve()
    if path.parent != jobs_dir(services).resolve():
        raise CronStoreError(f"job '{name}' is defined outside {jobs_dir(services)} ({path}); edit it there")
    return path


async def remove(services: Services, name: str, *, actor: str = "agent") -> None:
    path = removable(services, name)
    path.unlink(missing_ok=True)
    await _reload(services)
    await services.audit.record(actor=actor, action="cron.removed", decision="allow", detail={"job": name})
    await services.emit("server", "cron.removed", {"job": name, "actor": actor})
