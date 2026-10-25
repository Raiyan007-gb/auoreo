"""G1b: per-call secret scope (ContextVar) + name-only secret.read audit."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from auoreo.core.db import AuditRow
from auoreo.core.permissions import PermissionEngine
from auoreo.core.secrets import SECRET_SCOPE, SecretStore, UnscopedSecretError, secret_scope
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext


def test_scope_refuses_names_outside_requires_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOWED_TOKEN", "v-allowed-123456")
    monkeypatch.setenv("OTHER_TOKEN", "v-other-123456")
    store = SecretStore(tmp_path)
    seen: list[tuple[str, str, bool]] = []
    store.audit_hook = lambda name, by, ok: seen.append((name, by, ok))
    assert store.get("OTHER_TOKEN") == "v-other-123456"  # no scope (server code): allowed, unaudited
    assert seen == []
    with secret_scope("gh_api", ["ALLOWED_TOKEN"]):
        assert store.get("ALLOWED_TOKEN") == "v-allowed-123456"
        with pytest.raises(UnscopedSecretError, match="OTHER_TOKEN"):
            store.get("OTHER_TOKEN")
    assert SECRET_SCOPE.get() is None
    assert store.get("ALLOWED_TOKEN", by="plugin:telegram") == "v-allowed-123456"
    assert seen == [
        ("ALLOWED_TOKEN", "gh_api", True),
        ("OTHER_TOKEN", "gh_api", False),
        ("ALLOWED_TOKEN", "plugin:telegram", True),
    ]
    assert all("v-" not in str(row) for row in seen)  # names only, never values


async def test_executor_scopes_tool_and_audits_by_name(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auoreo.core.extensions import STARTERS
    from auoreo.core.secrets import start

    assert "auoreo.core.secrets:start" in STARTERS
    await start(services)
    monkeypatch.setenv("G1B_PROBE_TOKEN", "super-secret-value-0001")
    captured: dict[str, object] = {}

    async def handler(args: dict, ctx: ToolContext) -> str:
        captured["scope"] = SECRET_SCOPE.get()
        try:
            services.secrets.get("G1B_PROBE_TOKEN")
        except UnscopedSecretError as exc:
            return f"refused: {exc}"
        return "read it"

    tool = services.harness.tools["glob"]
    monkeypatch.setattr(tool, "_handler", handler)
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home, harness=services.harness)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "glob", json.dumps({"pattern": "*"})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine([Rule(permission="*", pattern="*", action="allow")]),
    )
    assert "refused" in out.result.content and captured["scope"].by == "glob"  # type: ignore[attr-defined]
    await asyncio.sleep(0.05)
    async with services.db.session() as s:
        rows = [r for r in (await s.execute(select(AuditRow))).scalars() if r.action == "secret.read"]
    assert rows and rows[-1].decision == "deny" and rows[-1].reason == "unscoped"
    detail = services.audit.plain_detail(rows[-1])
    assert detail == {"name": "G1B_PROBE_TOKEN", "by": "glob"} and "super-secret" not in str(detail)
