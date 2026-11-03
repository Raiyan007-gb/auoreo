"""The fake sandbox backend (`backends/sandbox/fake`): a deterministic stand-in for CI and
tests that need an *enforced* sandbox without bwrap, seatbelt or a Docker daemon."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from auoreo.backends.sandbox import ExecResult, ExecSpec
from auoreo.core.services import Services


def fake_backend(services: Services) -> Any:
    bdef = services.harness.select_backend("sandbox", "fake")
    assert bdef is not None
    return bdef.load()(services.settings)


async def test_fake_sandbox_records_specs_and_reports_enforcement(services: Services, workdir: Path) -> None:
    sb = fake_backend(services)
    assert (await sb.check()).ok
    assert sb.capabilities() == {"fs_isolation": True, "net_isolation": True}
    spec = ExecSpec(argv=["bash", "-c", "echo hi"], cwd=workdir, mode="read-only", net="none")
    res = await sb.run(spec)
    assert res == ExecResult(0, "echo hi", "", False, "fake", True)
    assert sb.calls == [spec]


async def test_fake_sandbox_plays_scripted_results_in_order(services: Services, workdir: Path) -> None:
    sb = fake_backend(services)
    denied = ExecResult(1, "", "bash: out: Read-only file system", False, "fake", True)
    sb.responses.append(denied)
    spec = ExecSpec(argv=["bash", "-c", "echo x > out"], cwd=workdir, mode="read-only")
    assert await sb.run(spec) == denied
    assert (await sb.run(spec)).ro_denied(spec) is False  # script consumed: back to the default
    assert len(sb.calls) == 2


async def test_fake_sandbox_never_wraps_long_lived_processes(services: Services, workdir: Path) -> None:
    sb = fake_backend(services)
    assert sb.wrap_long_lived(ExecSpec(argv=["bash", "-lc", "cat"], cwd=workdir)) is None


def test_fake_sandbox_is_never_auto_probed(services: Services) -> None:
    from auoreo.core import sandbox_boot

    assert "fake" not in sandbox_boot.auto_candidates(services.harness)
    assert all("fake" not in names for names in sandbox_boot.AUTO_ORDER.values())
