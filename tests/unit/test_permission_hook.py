"""`PermissionRequest` hooks (W1-A): fired on the ask path before the human is asked."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext
from tests.conftest import write

ASK_BASH = [Rule(permission="bash", pattern="*", action="ask")]


def hook(workdir: Path, name: str, script: str, *, on_error: str = "warn", matcher: str = "bash") -> None:
    root = workdir / ".auoreo"
    write(root / "scripts" / f"{name}.py", "import json, sys\npayload = json.load(sys.stdin)\n" + script)
    write(
        root / "hooks" / f"{name}.yaml",
        f"name: {name}\ndescription: test hook\nevent: PermissionRequest\nmatcher: '{matcher}'\n"
        f"command: ['{sys.executable}', './scripts/{name}.py']\non_error: {on_error}\n",
    )


async def bash(
    svc: Services, workdir: Path, command: str, **engine_kw: Any
) -> tuple[str, bool, PermissionEngine]:
    svc.harness.reload()
    ctx = ToolContext(
        session_id="ses_h", cwd=workdir, home=svc.settings.home, harness=svc.harness, interactive=False
    )
    engine = PermissionEngine(list(ASK_BASH), **engine_kw)
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", "bash", json.dumps({"command": command})),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=engine,
    )
    return out.result.content, out.result.is_error, engine


async def test_allow_grants_once_even_non_interactive(services: Services, workdir: Path) -> None:
    hook(
        workdir,
        "gate",
        "assert payload['permission'] == 'bash' and payload['tool'] == 'bash'\n"
        "ok = payload['pattern'].startswith('echo ')\n"
        "print(json.dumps({'decision': 'allow' if ok else 'deny', 'message': 'only echo'}))\n",
    )
    text, err, engine = await bash(services, workdir, "echo granted")
    assert not err and "granted" in text
    assert engine.approved == []  # a hook grant is never `always`
    text, err, _ = await bash(services, workdir, "make deploy")
    assert err and "only echo" in text


async def test_no_opinion_defers_to_the_human(services: Services, workdir: Path) -> None:
    hook(workdir, "quiet", "print('{}')\n")
    text, err, _ = await bash(services, workdir, "echo hi")
    assert err and "non-interactive" in text


async def test_errors_follow_on_error(services: Services, workdir: Path) -> None:
    hook(workdir, "crash", "sys.exit(3)\n", on_error="warn")
    text, err, _ = await bash(services, workdir, "echo hi")
    assert err and "non-interactive" in text  # warn: fall through, never a grant
    hook(workdir, "crash", "sys.exit(3)\n", on_error="block")
    text, err, _ = await bash(services, workdir, "echo hi")
    assert err and "PermissionRequest hook" in text and "on_error" in text


async def test_hook_never_sees_floors_or_allowed_calls(services: Services, workdir: Path) -> None:
    hook(workdir, "yes", "print(json.dumps({'decision': 'allow'}))\n")
    text, err, _ = await bash(services, workdir, "rm -rf /")
    assert err and "floor" in text
    services.harness.reload()
    ctx = ToolContext(session_id="ses_h", cwd=workdir, home=services.settings.home, harness=services.harness)
    allow = PermissionEngine([Rule(permission="*", pattern="*", action="allow")])
    out = await services.coordinator.runner.executor.run(
        ToolCall("c2", "bash", json.dumps({"command": "echo direct"})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=allow,
    )
    assert not out.result.is_error
    ran = await services.events.replay(0, frozenset({"session:ses_h"}), limit=1000)
    assert not [e for e in ran if e.type == "hook.ran"]
