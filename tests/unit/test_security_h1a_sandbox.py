"""H1a: the sandbox default is `auto` — bwrap (+Landlock) on Linux when it probes OK, `none`
only when nothing confining is usable (warned, audited, doctor warn) — spec §19.8."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from auoreo.core import sandbox_boot
from auoreo.core.db import AuditRow
from auoreo.core.diagnostics import backend_checks
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext

HAS_BWRAP = sys.platform.startswith("linux") and shutil.which("bwrap") is not None


def auto_settings(services: Services, **kw: object) -> Settings:
    return Settings(home=services.settings.home, gateway="fake", backends={"sandbox": "auto"}, **kw)  # type: ignore[arg-type]


async def fallback_rows(services: Services) -> list[AuditRow]:
    async with services.db.session() as s:
        rows = (await s.execute(select(AuditRow))).scalars().all()
    return [r for r in rows if r.action == "sandbox.fallback"]


@pytest.mark.skipif(not HAS_BWRAP, reason="needs bubblewrap on Linux")
async def test_auto_prefers_bwrap_and_runs_bash_confined(services: Services, workdir: Path) -> None:
    services.settings = auto_settings(services)
    boot = await sandbox_boot.start(services)
    assert boot is not None and boot.selected == "bwrap" and boot.enforced and not boot.fell_back
    assert services.backend("sandbox").name == "bwrap"
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "bash", json.dumps({"command": "echo confined-$((6*7))"})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine([Rule(permission="bash", pattern="*", action="allow")]),
    )
    assert not out.result.is_error and "confined-42" in out.result.content
    assert out.result.meta["sandbox"] == "bwrap" and out.result.meta["enforced"] is True
    rows = {c.check: c for c in await backend_checks(services.settings, services.harness, services)}
    assert rows["backend:sandbox"].level == "ok" and "bwrap" in rows["backend:sandbox"].detail


async def test_auto_without_a_confining_backend_warns_audits_and_runs_none(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox_boot, "AUTO_ORDER", {})
    services.settings = auto_settings(services)
    boot = await sandbox_boot.start(services)
    assert boot is not None and boot.selected == "auto" and services.backend("sandbox").name == "none"
    assert not boot.enforced
    rows = await fallback_rows(services)
    assert rows and rows[-1].detail == {"selected": "auto", "fallback": "none"}
    checks = {c.check: c for c in await backend_checks(services.settings, services.harness, services)}
    assert checks["backend:sandbox"].level == "warn" and "no isolation" in checks["backend:sandbox"].detail


async def test_auto_with_policy_fail_refuses_when_nothing_confines(
    services: Services, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox_boot, "AUTO_ORDER", {})
    services.settings = auto_settings(services, sandbox_policy="fail")
    with pytest.raises(sandbox_boot.SandboxStartupRefused, match="no confining sandbox"):
        await sandbox_boot.start(services)


async def test_unset_backend_means_auto(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUOREO_BACKENDS")  # tests/conftest.py pins `none` for the rest of the suite
    monkeypatch.setattr(sandbox_boot, "AUTO_ORDER", {})
    services.settings = Settings(home=services.settings.home, gateway="fake")
    assert services.settings.backends.get("sandbox") is None
    boot = await sandbox_boot.start(services)
    assert boot is not None and boot.selected == "auto"


async def test_explicit_none_is_honoured(services: Services) -> None:
    services.settings = Settings(home=services.settings.home, gateway="fake", backends={"sandbox": "none"})
    assert sandbox_boot.configured(services.settings, services.harness) == "none"
    boot = await sandbox_boot.start(services)
    assert boot is not None and boot.selected == "none" and not boot.fell_back


def test_auto_candidates_follow_the_platform(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    expected = ["bwrap"] if shutil.which("bwrap") else []
    assert sandbox_boot.auto_candidates(services.harness) == expected
    monkeypatch.setattr(sys, "platform", "win32")
    assert sandbox_boot.auto_candidates(services.harness) == []
