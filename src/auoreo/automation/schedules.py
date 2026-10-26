"""Schedule parsing (`0 9 * * 1-5` | `every 30m` | ISO one-shot) and catch-up arithmetic.

APScheduler 3.x numbers weekdays from Monday=0 while crontab uses Sunday=0, so the
day-of-week field is expanded with croniter (crontab semantics) and handed to
APScheduler as weekday names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from croniter import croniter
from tzlocal import get_localzone

EVERY_RE = re.compile(r"^every\s+(\d+)\s*(m|min|h|d)$", re.IGNORECASE)
BARE_RE = re.compile(r"^(\d+)\s*(m|min|h|d)$", re.IGNORECASE)  # I3: `30m` = every 30m
_DAYS = {"sun": "0", "mon": "1", "tue": "2", "wed": "3", "thu": "4", "fri": "5", "sat": "6"}
DAY_FIELD = {
    **_DAYS,
    **{
        full: _DAYS[full[:3]]
        for full in ("sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday")
    },
    "tues": "2",
    "thurs": "4",
    "day": "*",
    "weekday": "1-5",
    "weekend": "0,6",
}
WEEKLY_RE = re.compile(
    r"^every\s+(?P<day>" + "|".join(sorted(DAY_FIELD, key=len, reverse=True)) + r")"
    r"\s+(?:at\s+)?(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?(?:\s+(?P<tz>[A-Za-z_]+(?:/[A-Za-z_+-]+)*))?$",
    re.IGNORECASE,
)
UNIT_S = {"m": 60, "min": 60, "h": 3600, "d": 86_400}
DOW = ("sun", "mon", "tue", "wed", "thu", "fri", "sat", "sun")
MIN_GRACE_S, MAX_GRACE_S = 120.0, 7200.0


class ScheduleError(ValueError):
    pass


@dataclass(frozen=True)
class Schedule:
    kind: Literal["cron", "interval", "date"]
    expr: str
    trigger: Any  # apscheduler BaseTrigger
    tz: tzinfo
    period_s: float | None = None  # interval jobs; cron derives it per fire
    run_at: datetime | None = None  # one-shot

    def prev_fire(self, now: datetime, last: datetime | None) -> datetime | None:
        """Most recent scheduled fire time at or before `now`."""
        if self.kind == "cron":
            return croniter(self.expr, now.astimezone(self.tz)).get_prev(datetime)
        if self.kind == "interval" and self.period_s and last is not None:
            k = int((now - last).total_seconds() // self.period_s)
            return last + timedelta(seconds=k * self.period_s) if k >= 1 else None
        if self.kind == "date" and self.run_at is not None and self.run_at <= now:
            return self.run_at
        return None

    def period_at(self, fire: datetime) -> float | None:
        if self.kind == "cron":
            before = croniter(self.expr, fire.astimezone(self.tz)).get_prev(datetime)
            return (fire - before).total_seconds()
        return self.period_s

    def grace_s(self, fire: datetime | None = None) -> float:
        """`clamp(period/2, 120 s, 2 h)` — misfire grace and the catch-up window."""
        period = self.period_at(fire) if fire is not None else self.period_s
        if period is None and self.kind == "cron":
            period = self.period_at(datetime.now(UTC))
        if period is None:
            return MAX_GRACE_S
        return max(MIN_GRACE_S, min(period / 2, MAX_GRACE_S))

    def next_fire(self, now: datetime | None = None) -> datetime | None:
        now = now or datetime.now(UTC)
        nxt = self.trigger.get_next_fire_time(None, now.astimezone(self.tz))
        return nxt if isinstance(nxt, datetime) else None


def zone(name: str | None, job: str = "?") -> tzinfo:
    if not name:
        return get_localzone()
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ScheduleError(f"job '{job}': unknown time zone '{name}'") from exc


def _apscheduler_dow(expr: str) -> str:
    """Crontab day-of-week field → APScheduler weekday names (`1-5` → `mon,tue,wed,thu,fri`)."""
    expanded, _nth = croniter.expand(expr)
    days = expanded[4]
    if days == ["*"]:
        return "*"
    if not all(isinstance(d, int) for d in days):
        raise ScheduleError(f"unsupported day-of-week field in '{expr}'")
    return ",".join(dict.fromkeys(DOW[int(d)] for d in days))


def weekly(text: str) -> tuple[str, str | None] | None:
    """`every monday 9am [<tz>]` / `every weekday at 17:30 UTC` → (`MM HH * * DOW`, tz or None)."""
    m = WEEKLY_RE.match(text.strip())
    if m is None:
        return None
    day, hour, minute, ampm, tz = (
        m.group("day").lower(),
        int(m.group("h")),
        int(m.group("m") or 0),
        m.group("ampm"),
        m.group("tz"),
    )
    if ampm:
        if not 1 <= hour <= 12:
            raise ScheduleError(f"'{text}': hour {hour} is not valid with {ampm}")
        hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        raise ScheduleError(f"'{text}': invalid time of day")
    return f"{minute} {hour} * * {DAY_FIELD[day]}", tz


def canonical(text: str) -> tuple[str, str | None]:
    """Normalize at write time (spec §25.11): a bare duration `30m` → `every 30m`;
    `every monday 9am <tz>` → (`0 9 * * 1`, tz). Anything else is returned unchanged."""
    raw = text.strip()
    if BARE_RE.match(raw):
        return f"every {raw}", None
    return weekly(raw) or (raw, None)


def parse_schedule(schedule: str, tz: str | None = None, job: str = "?") -> Schedule:
    """Raises `ScheduleError` naming the job for anything it cannot schedule."""
    text, tz_in_text = canonical(schedule)
    tz = tz_in_text or tz
    zinfo = zone(tz, job)
    if m := EVERY_RE.match(text):
        seconds = int(m.group(1)) * UNIT_S[m.group(2).lower()]
        if seconds <= 0:
            raise ScheduleError(f"job '{job}': interval must be positive")
        return Schedule(
            "interval", text, IntervalTrigger(seconds=seconds, timezone=zinfo), zinfo, float(seconds)
        )
    if len(text.split()) == 5:
        if not croniter.is_valid(text):
            raise ScheduleError(f"job '{job}': invalid cron expression '{text}'")
        minute, hour, dom, month, _dow = text.split()
        try:
            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=dom,
                month=month,
                day_of_week=_apscheduler_dow(text),
                timezone=zinfo,
            )
        except ValueError as exc:
            raise ScheduleError(f"job '{job}': invalid cron expression '{text}': {exc}") from exc
        return Schedule("cron", text, trigger, zinfo)
    try:
        run_at = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ScheduleError(
            f"job '{job}': schedule '{text}' is not 5-field cron, 'every <n>m|h|d', or an ISO datetime"
        ) from exc
    if run_at.tzinfo is None:
        run_at = run_at.replace(tzinfo=zinfo)
    return Schedule("date", text, DateTrigger(run_date=run_at, timezone=zinfo), zinfo, run_at=run_at)


def missed_fire(sched: Schedule, last: datetime | None, now: datetime) -> datetime | None:
    """The fire time to catch up on, or None: there is a previous run, a scheduled fire
    after it was missed, and that fire is within `clamp(period/2, 120 s, 2 h)` of now."""
    if last is None:
        return None
    prev = sched.prev_fire(now, last)
    if prev is None or prev <= last:
        return None
    if (now - prev).total_seconds() > sched.grace_s(prev):
        return None
    return prev
