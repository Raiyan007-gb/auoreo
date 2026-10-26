"""I1 creator-mode hardening (spec §15 L642/L648): an "Always" grant is keyed by the immutable
package hash (redefine-and-run cannot reuse it), definitions are versioned packages, inspect
comes before define, rejected packages are tombstoned and audit rows carry `plugin_run_id`."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from auoreo.core.db import AuditRow
from auoreo.core.services import Services
from auoreo.plugins.creator import manager, tool_name_for
from tests.unit.test_creator import CODE, SID, call, make_ctx

EVIL = """
async def run(args, ctx):
    return "evil"
"""


async def _define(svc: Services, ctx: object, code: str = CODE, name: str = "adder") -> str:
    res = await call(svc, ctx, "plugin_define", {"name": name, "description": "adds", "code": code})  # type: ignore[arg-type]
    assert not res.is_error, res.content
    return res.content


async def test_define_requires_inspect_first(services: Services, workdir: Path) -> None:
    ctx = make_ctx(services, workdir)
    res = await call(services, ctx, "plugin_define", {"name": "adder", "description": "x", "code": CODE})
    assert res.is_error and "plugin_inspect first" in res.content
    await call(services, ctx, "plugin_inspect", {})
    await _define(services, ctx)


async def test_always_grant_does_not_survive_a_redefinition(services: Services, workdir: Path) -> None:
    ctx = make_ctx(services, workdir, interactive=False)
    engine = ctx.services["engine"]
    await call(services, ctx, "plugin_inspect", {})
    await _define(services, ctx)
    mgr = manager(services)
    first = mgr.get(SID, "adder")
    engine.grant_always("plugin_run", first.grant_key)  # what an "Always" reply stores
    assert not (await call(services, ctx, "plugin_run", {"name": "adder"})).is_error
    await call(services, ctx, "plugin_stop", {"name": "adder"})

    out = await _define(services, ctx, EVIL)  # same name, new code → new immutable package
    second = mgr.get(SID, "adder")
    assert "v2" in out and second.version == 2 and second.package_id != first.package_id
    res = await call(services, ctx, "plugin_run", {"name": "adder"})
    assert res.is_error and "not approved" in res.content  # the old grant does not cover it
    assert tool_name_for(SID, "adder") not in services.harness.tools

    # identical content is the same package: a no-op, not a new version
    await _define(services, ctx, EVIL)
    assert mgr.get(SID, "adder") is second


async def test_rejected_package_is_tombstoned(services: Services, workdir: Path) -> None:
    asked: list[str] = []

    async def deny(permission: str, pattern: str, detail: str) -> bool:
        asked.append(pattern)
        return False

    ctx = make_ctx(services, workdir, ask=deny)
    await call(services, ctx, "plugin_inspect", {})
    await _define(services, ctx)
    res = await call(services, ctx, "plugin_run", {"name": "adder"})
    assert res.is_error and "rejected" in res.content
    res = await call(services, ctx, "plugin_run", {"name": "adder"})
    assert res.is_error and "define a new version" in res.content
    assert len(asked) == 1  # no retry-after-reject: the human is not asked again

    # undefine + redefine the very same code is still tombstoned
    await call(services, ctx, "plugin_undefine", {"name": "adder"})
    await _define(services, ctx)
    res = await call(services, ctx, "plugin_run", {"name": "adder"})
    assert res.is_error and len(asked) == 1


async def test_audit_rows_carry_package_and_run_ids(services: Services, workdir: Path) -> None:
    async def ok(permission: str, pattern: str, detail: str) -> bool:
        return True

    ctx = make_ctx(services, workdir, ask=ok)
    await call(services, ctx, "plugin_inspect", {})
    await _define(services, ctx)
    assert not (await call(services, ctx, "plugin_run", {"name": "adder"})).is_error
    await call(services, ctx, "plugin_stop", {"name": "adder"})
    async with services.db.session() as s:
        rows = list((await s.execute(select(AuditRow).where(AuditRow.actor == "creator"))).scalars())
    run = next(r for r in rows if r.action == "creator.run")
    assert run.detail["plugin_run_id"].startswith("prun_")
    assert run.detail["package_id"].startswith("pkg_") and run.detail["plugin_id"] == tool_name_for(
        SID, "adder"
    )
    define = next(r for r in rows if r.action == "creator.define")
    assert define.detail["plugin_run_id"] is None and define.detail["version"] == 1
