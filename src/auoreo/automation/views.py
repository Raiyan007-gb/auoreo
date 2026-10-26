"""Row → wire-model conversions shared by the API routers and the CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from auoreo.automation.scheduler import JobScheduler
from auoreo.automation.webhooks import secret_name
from auoreo.core.db import DeliveryRow, JobRunRow
from auoreo.schema.chat import CronJobInfo, CronRunInfo, DeliveryInfo, WebhookRouteInfo

if TYPE_CHECKING:
    from auoreo.core.services import Services


def run_info(row: JobRunRow) -> CronRunInfo:
    return CronRunInfo(
        id=row.id,
        job=row.job,
        scheduled_for=row.scheduled_for,
        started_at=row.started_at,
        finished_at=row.finished_at,
        status=row.status,
        session_id=row.session_id,
        catch_up=bool(row.catch_up),
        error=row.error,
    )


async def job_infos(sched: JobScheduler) -> list[CronJobInfo]:
    paused = sched.paused()
    out: list[CronJobInfo] = []
    for name, m in sorted(sched.manifests().items()):
        last = await sched.last_run(name)
        out.append(
            CronJobInfo(
                name=name,
                description=m.description,
                schedule=m.schedule,
                tz=m.tz,
                agent=m.agent,
                deliver=list(m.deliver),
                enabled=m.enabled,
                paused=name in paused,
                error=sched.errors.get(name),
                next_run_at=sched.next_run(name),
                last_run=run_info(last) if last else None,
            )
        )
    return out


def delivery_info(row: DeliveryRow) -> DeliveryInfo:
    return DeliveryInfo(
        id=row.id,
        kind=row.kind,
        source=row.source,
        external_id=row.external_id,
        status=row.status,
        session_id=row.session_id,
        target=row.target,
        attempts=row.attempts,
        error=row.error,
        result=row.result or {},
        created_at=row.created_at,
        updated_at=row.updated_at,
        claimed_at=row.claimed_at,
    )


def webhook_infos(services: Services) -> list[WebhookRouteInfo]:
    out: list[WebhookRouteInfo] = []
    for name, d in sorted(services.harness.webhooks.items()):
        m = d.manifest
        out.append(
            WebhookRouteInfo(
                name=name,
                path=f"/hooks/{name}",
                description=m.description,
                provider=m.provider,
                events=list(m.events),
                filters=[f.model_dump(exclude_none=True, by_alias=True) for f in m.filters],
                agent=m.agent,
                workflow=m.workflow,
                deliver=list(m.deliver),
                enabled=m.enabled and services.settings.webhooks_enabled,
                rate_limit_per_minute=m.rate_limit_per_minute,
                max_body_bytes=m.max_body_bytes,
                secret_configured=bool(services.secrets.get(secret_name(m))),
            )
        )
    return out
