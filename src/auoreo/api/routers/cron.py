"""Scheduled jobs: list, run now, pause/resume, run history."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.automation.scheduler import JobScheduler, scheduler_of
from auoreo.automation.views import job_infos, run_info
from auoreo.schema.chat import CronJobInfo, CronPaused, CronRunAccepted, CronRunInfo

router = APIRouter(prefix="/api/cron", tags=["cron"], dependencies=AUTH)


def _job(svc: ServicesDep, name: str) -> JobScheduler:
    sched = scheduler_of(svc)
    if name not in sched.manifests():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown job '{name}'")
    return sched


@router.get("", response_model=list[CronJobInfo])
async def list_jobs(svc: ServicesDep) -> list[CronJobInfo]:
    return await job_infos(scheduler_of(svc))


@router.post("/{name}/run", response_model=CronRunAccepted, status_code=status.HTTP_202_ACCEPTED)
async def run_job(name: str, svc: ServicesDep) -> CronRunAccepted:
    """Fire now (outside the schedule); the run continues in the background."""
    sched = _job(svc, name)
    run_id = await sched.start_run(name)
    if run_id is None:  # pragma: no cover - a manual run's timestamp is unique
        raise HTTPException(status.HTTP_409_CONFLICT, "a run for this instant already exists")
    sched.run_now(run_id)
    return CronRunAccepted(job=name, run_id=run_id, status="pending")


@router.post("/{name}/pause", response_model=CronPaused)
async def pause_job(name: str, svc: ServicesDep) -> CronPaused:
    await _job(svc, name).set_paused(name, True)
    return CronPaused(job=name, paused=True)


@router.post("/{name}/resume", response_model=CronPaused)
async def resume_job(name: str, svc: ServicesDep) -> CronPaused:
    await _job(svc, name).set_paused(name, False)
    return CronPaused(job=name, paused=False)


@router.get("/{name}/runs", response_model=list[CronRunInfo])
async def job_runs(name: str, svc: ServicesDep, limit: int = 20) -> list[CronRunInfo]:
    return [run_info(r) for r in await _job(svc, name).runs(name, limit)]
