"""One-call sandbox escalation (spec §19.8 / §24.9) through the executor's ask path."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.backends import BackendStatus
from auoreo.backends.sandbox import ExecResult, ExecSpec, SandboxBackend, justification_error
from auoreo.core.db import AuditRow
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext

BASH_ALLOW = [Rule(permission="bash", pattern="*", action="allow")]
GOOD = "write build artifacts to /opt/cache/pip for the install step"


class Recorder(SandboxBackend):
    """Records specs; optionally pretends an enforced read-only sandbox blocked a write."""

    name = "recorder"

    def __init__(self, settings: Any, ro_block: bool = False) -> None:
        super().__init__(settings)
        self.specs: list[ExecSpec] = []
        self.ro_block = ro_block

    async def check(self) -> BackendStatus:
        return BackendStatus(True)

    def capabilities(self) -> dict[str, bool]:
        return {"fs_isolation": True, "net_isolation": True}

    async def run(self, spec: ExecSpec) -> ExecResult:
        self.specs.append(spec)
        if self.ro_block and spec.mode == "read-only":
            return ExecResult(
                1, "touch: cannot touch 'x': Read-only file system\n", "", False, self.name, True
            )
        return ExecResult(0, "ok\n", "", False, self.name, True)


@pytest.fixture
def recorder(services: Services) -> Recorder:
    rec = Recorder(services.settings)
    services._backends["sandbox"] = rec
    return rec


async def run_bash(
    services: Services, ctx: ToolContext, engine: PermissionEngine, **args: Any
) -> tuple[str, bool]:
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "bash", json.dumps({"command": "touch x", **args})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=engine,
    )
    return out.result.content, out.result.is_error


async def make_ctx(services: Services, workdir: Path, interactive: bool = True) -> ToolContext:
    sess = await services.create_session()
    return ToolContext(session_id=sess.id, cwd=workdir, home=services.settings.home, interactive=interactive)


async def answer(services: Services, sid: str, reply: str) -> str:
    while not services.approvals.pending(sid):
        await asyncio.sleep(0.01)
    p = services.approvals.pending(sid)[0]
    await services.approvals.reply(p.request_id, reply)  # type: ignore[arg-type]
    return f"{p.permission}|{p.pattern}"


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("", False),
        ("too short", False),
        ("need access", False),
        ("Need access.", False),
        ("I need access", False),
        ("please allow this one more time ok", False),
        ("x" * 501, False),
        ("write build artifacts to ./dist", True),
        ("install deps into ~/.cache/pip", True),
        ("need to create /tmp/out.txt for the report", True),
        ("git clone needs to write outside", True),
    ],
)
def test_justification_table(text: str, ok: bool) -> None:
    assert (justification_error(text) is None) is ok


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({"sandbox_mode": "danger-full-access"}, "10–500"),
        ({"sandbox_mode": "danger-full-access", "justification": "need access"}, "generic"),
        ({"sandbox_mode": "danger-full-access", "justification": "    "}, "10–500"),
        ({"justification": GOOD}, "without a sandbox_mode"),
        ({"sandbox_mode": "workspace-write", "justification": GOOD}, "without a mode bump"),
        ({"sandbox_mode": "read-only", "justification": GOOD}, "without a mode bump"),
    ],
)
async def test_bad_requests_fail_without_a_prompt(
    services: Services, workdir: Path, recorder: Recorder, args: dict[str, Any], needle: str
) -> None:
    ctx = await make_ctx(services, workdir)
    text, err = await run_bash(services, ctx, PermissionEngine(BASH_ALLOW), **args)
    assert err and needle in text
    assert not services.approvals.pending(ctx.session_id) and not recorder.specs


async def test_grant_applies_to_one_call_only(services: Services, workdir: Path, recorder: Recorder) -> None:
    ctx = await make_ctx(services, workdir)
    engine = PermissionEngine(BASH_ALLOW)
    args = {"sandbox_mode": "danger-full-access", "justification": GOOD}
    responder = asyncio.create_task(answer(services, ctx.session_id, "always"))
    text, err = await run_bash(services, ctx, engine, **args)
    asked = await responder
    assert not err, text
    assert asked == f"sandbox_escalate|danger-full-access:{GOOD[:60]}"
    assert recorder.specs[-1].mode == "danger-full-access"
    assert engine.approved == []  # `always` never sticks for escalations

    # The next call runs at the manifest mode again …
    await run_bash(services, ctx, engine)
    assert recorder.specs[-1].mode == "workspace-write"
    # … and the same escalation asks again.
    responder = asyncio.create_task(answer(services, ctx.session_id, "once"))
    await run_bash(services, ctx, engine, **args)
    await responder
    assert recorder.specs[-1].mode == "danger-full-access"

    async with services.db.session() as s:
        rows = list(
            (await s.execute(select(AuditRow).where(AuditRow.action == "sandbox.escalate"))).scalars()
        )
    assert len(rows) == 2
    d = rows[0].detail
    assert (d["fromMode"], d["toMode"]) == ("workspace-write", "danger-full-access")
    assert len(d["justificationHash"]) == 16 and GOOD not in json.dumps(d)
    assert rows[0].detail["grantId"] != rows[1].detail["grantId"]


async def test_rejected_escalation_does_not_run(
    services: Services, workdir: Path, recorder: Recorder
) -> None:
    ctx = await make_ctx(services, workdir)
    responder = asyncio.create_task(answer(services, ctx.session_id, "reject"))
    text, err = await run_bash(
        services, ctx, PermissionEngine(BASH_ALLOW), sandbox_mode="danger-full-access", justification=GOOD
    )
    await responder
    assert err and not recorder.specs


async def test_non_interactive_escalation_is_refused(
    services: Services, workdir: Path, recorder: Recorder
) -> None:
    ctx = await make_ctx(services, workdir, interactive=False)
    text, err = await run_bash(
        services, ctx, PermissionEngine(BASH_ALLOW), sandbox_mode="danger-full-access", justification=GOOD
    )
    assert err and "non-interactive" in text and not recorder.specs


async def test_narrower_mode_needs_no_grant(services: Services, workdir: Path, recorder: Recorder) -> None:
    ctx = await make_ctx(services, workdir, interactive=False)
    text, err = await run_bash(services, ctx, PermissionEngine(BASH_ALLOW), sandbox_mode="read-only")
    assert not err and recorder.specs[-1].mode == "read-only"


async def test_read_only_denial_text(services: Services, workdir: Path) -> None:
    rec = Recorder(services.settings, ro_block=True)
    services._backends["sandbox"] = rec
    ctx = await make_ctx(services, workdir, interactive=False)
    text, err = await run_bash(services, ctx, PermissionEngine(BASH_ALLOW), sandbox_mode="read-only")
    assert err
    assert (
        '[sandbox: file access denied under read-only mode] — retry with sandbox_mode="workspace-write" '
        "and a justification"
    ) in text
    assert "not enforced" not in text  # the recorder claims enforcement, so no note


async def test_yolo_never_grants_a_wider_mode(
    services: Services, workdir: Path, recorder: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auoreo.core import permissions as perm

    monkeypatch.setattr(perm, "YOLO_FROZEN", True)
    engine = PermissionEngine(BASH_ALLOW, mode="yolo")
    ctx = await make_ctx(services, workdir, interactive=False)
    text, err = await run_bash(services, ctx, engine, sandbox_mode="danger-full-access", justification=GOOD)
    assert err and "non-interactive" in text and not recorder.specs
