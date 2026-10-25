"""ops_* tools (psutil) return compact markdown readings."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import ToolContext

OPS = ["ops_cpu", "ops_mem", "ops_disk", "ops_net", "ops_procs"]


async def call(services: Services, workdir: Path, name: str, args: dict) -> tuple[str, bool]:
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine([]),  # no rules: the manifests' default_action (allow) applies
    )
    return out.result.content, out.result.is_error


@pytest.mark.parametrize("name", [*OPS, "db_query"])
def test_ops_manifests(services: Services, name: str) -> None:
    m = services.harness.tools[name].manifest
    assert (m.toolset, m.exposure, m.permission_name, m.default_action) == ("ops", "deferred", "ops", "allow")
    assert m.is_read_only and m.is_concurrency_safe and m.net == "none"


async def test_ops_cpu(services: Services, workdir: Path) -> None:
    text, err = await call(services, workdir, "ops_cpu", {"interval_s": 0.1})
    assert not err and text.startswith("**CPU**") and "logical" in text


async def test_ops_mem(services: Services, workdir: Path) -> None:
    text, err = await call(services, workdir, "ops_mem", {})
    assert not err and "**Memory**" in text and "GiB" in text and "**Swap**" in text


async def test_ops_disk(services: Services, workdir: Path) -> None:
    text, err = await call(services, workdir, "ops_disk", {})
    assert not err and f"`{workdir}`" in text and "| mount |" in text
    text, err = await call(services, workdir, "ops_disk", {"path": "does/not/exist"})
    assert err and "does not exist" in text


async def test_ops_net(services: Services, workdir: Path) -> None:
    text, err = await call(services, workdir, "ops_net", {})
    assert not err and "**Network**" in text and "| interface |" in text


async def test_ops_procs_sort_limit_filter(services: Services, workdir: Path) -> None:
    text, err = await call(services, workdir, "ops_procs", {"limit": 3, "sort": "mem"})
    assert not err and "by mem" in text
    rows = [ln for ln in text.splitlines() if ln.startswith("| ") and not ln.startswith("| pid")]
    assert 1 <= len(rows) <= 3
    text, _ = await call(services, workdir, "ops_procs", {"filter": "python", "limit": 100})
    assert f"| {os.getpid()} |" in text
    text, _ = await call(services, workdir, "ops_procs", {"filter": "no-such-process-zzz"})
    assert "0 matched" in text
