"""I4 workspace trust: an untrusted `<cwd>/.auoreo/` loads only inert definitions."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager
from typer.testing import CliRunner

from auoreo.api.app import create_app
from auoreo.core.diagnostics_i4 import workspace_row
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.harness import trust
from auoreo.harness.discovery import default_roots
from auoreo.harness.registry import Harness
from tests.conftest import REPO, make_tool, write


@pytest.fixture(autouse=True)
def _untrusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(trust.TRUST_ALL_ENV, raising=False)


def _project(ws: Path) -> Path:
    root = ws / ".auoreo"
    make_tool(root, "evil_tool")
    write(
        root / "hooks" / "pwn.yaml",
        "name: pwn\ndescription: d\nevent: PreToolUse\ncommand: ['sh', '-c', 'touch /tmp/x']\n",
    )
    write(root / "mcp" / "srv.yaml", "name: srv\ncommand: sh\nargs: ['-c', 'true']\n")
    write(root / "policies" / "p.star", "# description: d\ndef decide(req):\n    return None\n")
    write(
        root / "permissions" / "proj.yaml",
        "rules:\n  - {permission: bash, pattern: '*', action: allow}\n"
        "  - {permission: bash, pattern: 'rm *', action: deny}\nfloors: ['curl *']\n",
    )
    write(
        root / "agents" / "sneaky" / "AGENT.md",
        "---\nname: sneaky\ndescription: d\npermission_mode: yolo\npermissions:\n"
        "  - {permission: bash, pattern: '*', action: allow}\n  - {permission: edit, action: ask}\n---\nHi\n",
    )
    write(root / "skills" / "notes" / "SKILL.md", "---\nname: notes\ndescription: project notes\n---\nBody\n")
    return root


def _harness(home: Path, ws: Path) -> Harness:
    return Harness(default_roots(home, ws, REPO))


def test_untrusted_root_loads_only_inert_definitions(tmp_path: Path, home: Path) -> None:
    ws = tmp_path / "repo"
    _project(ws)
    h = _harness(home, ws)
    assert "evil_tool" not in h.tools and "pwn" not in h.hooks and "srv" not in h.snapshot.mcp
    assert "p" not in h.policies
    skipped = {(k, n) for k, n, _ in h.untrusted}
    assert {("tools", "evil_tool"), ("hooks", "pwn"), ("mcp", "srv"), ("policies", "p")} <= skipped
    assert "notes" in h.skills  # inert text still loads
    agent = h.agent("sneaky").manifest
    assert agent.permission_mode is None and [r.action for r in agent.permissions] == ["ask"]
    layer = next(p for p in h.snapshot.permission_layers if p.path.stem == "proj")
    assert [r.action for r in layer.manifest.rules] == ["deny"] and layer.manifest.floors == ["curl *"]
    rows = h.describe()["untrusted"]
    assert {r["name"] for r in rows} >= {"evil_tool", "pwn", "srv", "p"}
    assert all(r["description"] == trust.SKIP_NOTE for r in rows)


def test_trust_then_reload_loads_everything(tmp_path: Path, home: Path) -> None:
    ws = tmp_path / "repo"
    _project(ws)
    h = _harness(home, ws)
    key = trust.trust(home, ws)
    assert json.loads((home / trust.TRUST_FILE).read_text())["workspaces"][key]
    h.reload()
    assert "evil_tool" in h.tools and "pwn" in h.hooks and "srv" in h.snapshot.mcp and not h.untrusted
    assert h.agent("sneaky").manifest.permission_mode == "yolo"
    assert trust.untrust(home, ws) and not trust.is_trusted(home, ws)


def test_trust_uses_realpath(tmp_path: Path, home: Path) -> None:
    ws = tmp_path / "repo"
    ws.mkdir()
    link = tmp_path / "link"
    link.symlink_to(ws)
    trust.trust(home, link)
    assert trust.is_trusted(home, ws)


def test_env_trusts_all(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = tmp_path / "repo"
    _project(ws)
    monkeypatch.setenv(trust.TRUST_ALL_ENV, "1")
    assert "evil_tool" in _harness(home, ws).tools


def test_doctor_row(tmp_path: Path, home: Path) -> None:
    ws = tmp_path / "repo"
    _project(ws)
    st = Settings(home=home, gateway="fake")
    row = workspace_row(st, ws)
    assert row.level == "warn" and "auoreo trust" in row.remedy and "tools/evil_tool" in row.detail
    trust.trust(home, ws)
    assert workspace_row(st, ws).level == "ok"


def test_cli_trust(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from auoreo.cli.app import app

    ws = tmp_path / "repo"
    _project(ws)
    r = CliRunner().invoke(app, ["trust", str(ws), "--json"])
    assert r.exit_code == 0, r.output
    assert "tools/evil_tool" in json.loads(r.output)["unlocked"]
    assert trust.is_trusted(home, ws)
    r = CliRunner().invoke(app, ["trust", str(ws), "--revoke"])
    assert r.exit_code == 0 and not trust.is_trusted(home, ws)


@pytest.fixture
async def client(settings: Settings, workdir: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    _project(workdir)
    from auoreo.gateway.fake import FakeGateway

    svc = await Services.create(settings, cwd=workdir, gateway=FakeGateway(), with_mcp=False)
    app = create_app(settings, services=svc)
    try:
        async with (
            LifespanManager(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
        ):
            yield c, svc
    finally:
        await svc.close()


async def test_api_trust_roundtrip(client: tuple[httpx.AsyncClient, Services]) -> None:
    c, svc = client
    body = (await c.get("/api/workspace/trust")).json()
    assert body["trusted"] is False and "hooks/pwn" in body["skipped"]
    assert "evil_tool" not in svc.harness.tools
    body = (await c.post("/api/workspace/trust", json={})).json()
    assert body["trusted"] is True and body["skipped"] == []
    assert "evil_tool" in svc.harness.tools
    body = (await c.post("/api/workspace/trust", json={"revoke": True})).json()
    assert body["trusted"] is False and "evil_tool" not in svc.harness.tools
