"""I9 ops pieces: tool catalog guard, uvicorn ws pinning + loopback ping defaults, /metrics ws
counters, doctor rows (egress proxy, ws server, reverse-proxy probe), upgrade holder lock +
rollback schema guard + receipt steps, guardian timeout setting, nginx snippet, CI hygiene."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import yaml

from auoreo.core import diagnostics, diagnostics_i9, metrics_text, upgrade_guard  # noqa: F401  (import order)
from auoreo.core import upgrade as up
from auoreo.core.connections import ConnectionManager
from auoreo.core.guardian import timeout_s
from auoreo.core.settings import Settings
from auoreo.core.ws_server import explicit_ping, impl_available, ws_kwargs

ROOT = Path(__file__).resolve().parents[2]


def _script(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ tool catalog
def test_tool_catalog_is_current_and_detects_drift(tmp_path: Path) -> None:
    cat = _script("verify_tool_catalog")
    assert cat.problems() == []
    (tmp_path / "tools" / "zz_new").mkdir(parents=True)
    (tmp_path / "tools" / "zz_new" / "tool.yaml").write_text("name: zz_new\ndescription: d\nnet: proxy\n")
    stale = tmp_path / "tools.md"
    stale.write_text(cat.HEADER)
    found = cat.problems(tmp_path, stale)
    assert "undocumented tool: zz_new" in found and any("stale" in p for p in found)


# ------------------------------------------------------------------ ws server
def test_ws_pinning_and_loopback_ping_defaults(home: Path) -> None:
    loop = Settings(home=home)
    assert ws_kwargs(loop) == {"ws": "websockets-sansio", "ws_ping_interval": None, "ws_ping_timeout": None}
    lan = Settings(home=home, host="0.0.0.0")
    assert ws_kwargs(lan) == {"ws": "websockets-sansio", "ws_ping_interval": 20.0, "ws_ping_timeout": 20.0}
    explicit_ping(loop, 5, None)
    assert ws_kwargs(loop)["ws_ping_interval"] == 5
    assert impl_available("websockets-sansio")[0] and not impl_available("nope")[0]


def test_metrics_export_ws_counters() -> None:
    conns = ConnectionManager()
    conns.binary_dropped, conns.pings_sent, conns.keepalive_closes = 3, 7, 1
    text = metrics_text.render_ws(conns)
    assert "ws_binary_dropped_total 3" in text and "ws_pings_sent_total 7" in text
    assert "ws_keepalive_closes_total 1" in text and "ws_connections_active 0" in text
    assert "# TYPE ws_connections_active gauge" in text


async def test_metrics_route_includes_ws_counters(services: Any) -> None:
    from auoreo.api.app import create_app

    services.connections.binary_dropped = 2
    from asgi_lifespan import LifespanManager

    app = create_app(services.settings, services=services)
    transport = httpx.ASGITransport(app=app)
    async with LifespanManager(app), httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        res = await c.get("/metrics")
    assert res.status_code == 200 and "ws_binary_dropped_total 2" in res.text


# ------------------------------------------------------------------ doctor rows
async def test_doctor_i9_rows(home: Path) -> None:
    rows = {c.check: c for c in await diagnostics_i9.i9_checks(Settings(home=home))}
    assert rows["egress-proxy"].level == "ok" and "407" in rows["egress-proxy"].detail
    assert rows["ws-server"].level == "ok" and "websockets-sansio" in rows["ws-server"].detail
    assert "egress-sandbox" in rows and "search" in rows and "reverse-proxy" not in rows


async def test_reverse_proxy_probe(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUOREO_OFFLINE", raising=False)
    st = Settings(home=home)
    st.i9.reverse_proxy_url = "https://agent.example"

    def good(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"cache-control": "no-cache"}, content=b": ping\n\n")

    def buffered(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"cache-control": "max-age=60", "x-accel-buffering": "yes"}, content=b"x"
        )

    real = httpx.AsyncClient
    for handler, level in ((good, "ok"), (buffered, "error")):
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda *a, h=handler, **kw: real(transport=httpx.MockTransport(h), **kw)
        )
        (row,) = await diagnostics_i9.reverse_proxy_rows(st)
        assert row.level == level, row.detail
    assert "no-cache" in row.detail and "buffering" in row.detail


# ------------------------------------------------------------------ upgrade
def _db(home: Path, version: int) -> None:
    home.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(home / "state.db")
    con.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO schema_meta VALUES ('schema_version', ?)", (str(version),))
    con.commit()
    con.close()


def test_rollback_schema_guard_and_receipt(tmp_path: Path) -> None:
    home, binary = tmp_path / "home", tmp_path / "bin" / "auoreo"
    binary.parent.mkdir()
    binary.write_text("new")
    up.prev_path(binary).write_text("old")
    _db(home, 9)
    up.write_receipt(home, {"action": "installed", "from": "0.1.0", "to": "0.2.0", "from_schema": 7})
    with pytest.raises(up.UpgradeError, match=r"schema v9 .* only knows v7"):
        up.rollback(home, binary)
    assert binary.read_text() == "new"  # nothing swapped
    msg = up.rollback(home, binary, force=True)
    assert binary.read_text() == "old" and "restored" in msg
    receipt = up.read_receipt(home) or {}
    assert receipt["action"] == "rollback" and receipt["steps"][0] == "schema_guard"
    assert receipt["db_schema"] == 9 and receipt["skips"] == []


def test_holder_lock_is_exclusive(tmp_path: Path) -> None:
    with (
        upgrade_guard.holder_lock(tmp_path),
        pytest.raises(upgrade_guard.LockHeld),
        upgrade_guard.holder_lock(tmp_path),
    ):
        pass
    with upgrade_guard.holder_lock(tmp_path):  # released
        pass


def test_db_schema_reads_without_a_database(tmp_path: Path) -> None:
    assert upgrade_guard.db_schema(tmp_path) is None
    assert upgrade_guard.rollback_refusal({"from_schema": 1}, tmp_path) is None


# ------------------------------------------------------------------ guardian timeout
def test_guardian_timeout_setting(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert timeout_s(SimpleNamespace(settings=Settings(home=home))) == 15.0  # type: ignore[arg-type]
    monkeypatch.setenv("AUOREO_SMART_TIMEOUT", "4.5")
    assert timeout_s(SimpleNamespace(settings=Settings(home=home))) == 4.5  # type: ignore[arg-type]
    st = Settings(home=home)
    st.smart_timeout = None
    st.i9.guardian.timeout_s = 9
    assert timeout_s(SimpleNamespace(settings=st)) == 9.0  # type: ignore[arg-type]
    from auoreo.core import env_catalog_data
    from auoreo.core.env_catalog import by_name

    assert "AUOREO_SMART_TIMEOUT" in by_name()  # the catalog feeds Settings → Environment
    assert "smart_policy" in Path(env_catalog_data.__file__).read_text(encoding="utf-8")


# ------------------------------------------------------------------ deploy + CI hygiene
def test_nginx_snippet_covers_sse_ws_and_hooks() -> None:
    conf = (ROOT / "deploy" / "nginx" / "auoreo.conf").read_text()
    sse = conf.split("location /api/events", 1)[1].split("}", 1)[0]
    assert "proxy_buffering off" in sse and "proxy_read_timeout 3600s" in sse and "gzip off" in sse
    ws = conf.split("location /ws", 1)[1].split("}", 1)[0]
    assert "Upgrade" in ws and "60s" in ws
    assert "client_max_body_size 1m" in conf.split("location /hooks/", 1)[1]
    assert "X-Forwarded-For   $remote_addr" in conf  # set, never appended


def test_workflows_pin_actions_by_sha() -> None:
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for line in wf.read_text().splitlines():
            if line.strip().startswith(("- uses:", "uses:")):
                ref = line.split("uses:", 1)[1].strip()
                assert "@" in ref and len(ref.split("@", 1)[1].split()[0]) == 40, f"{wf.name}: {ref}"
                assert "# v" in ref, f"{wf.name}: {ref} lacks a version comment"


def test_ci_has_ty_e2e_and_catalog_jobs_and_dependabot() -> None:
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    jobs = ci["jobs"]
    assert jobs["ty"]["continue-on-error"] is True
    assert any("playwright install --with-deps" in str(s.get("run", "")) for s in jobs["e2e"]["steps"])
    assert any("verify_tool_catalog.py --check" in str(s.get("run", "")) for s in jobs["test"]["steps"])
    dep = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert {u["package-ecosystem"] for u in dep["updates"]} == {"uv", "npm", "github-actions"}
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"]
    ids = {h["id"] for r in hooks for h in r["hooks"]}
    assert {"gitleaks", "ruff-check", "ruff-format", "ty"} <= ids


def test_release_has_npm_and_desktop_jobs() -> None:
    rel = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    jobs = rel["jobs"]
    npm = json.dumps(jobs["npm"])
    assert jobs["npm"]["permissions"]["id-token"] == "write" and "--provenance" in npm
    assert "NPM_TOKEN" in npm and "version == tag" in npm
    desk = jobs["desktop"]
    assert {m["os"] for m in desk["strategy"]["matrix"]["include"]} >= {"macos-14", "windows-latest"}
    assert "AUOREO_CHANNEL" in desk["env"]
    for step in desk["steps"]:
        if "signing" in step.get("name", ""):
            assert "!= ''" in step["if"], step["name"]
    assert "scripts/install.sh" in json.dumps(jobs["github-release"])
