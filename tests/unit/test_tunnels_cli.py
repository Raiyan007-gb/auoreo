"""Tunnels (fake provider, monkeypatched `shutil.which`) and the M3 CLI commands."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.core.tunnels import FAKE_URL, TunnelError, TunnelManager, ws_url

runner = CliRunner()


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, workdir: Path) -> Path:
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    monkeypatch.chdir(workdir)
    return workdir


# ---------------------------------------------------------------------- tunnels
def test_ws_url() -> None:
    assert ws_url("https://a.trycloudflare.com/") == "wss://a.trycloudflare.com/ws"
    assert ws_url("http://127.0.0.1:4096") == "ws://127.0.0.1:4096/ws"


def test_start_refuses_without_password(home: Path) -> None:
    with pytest.raises(TunnelError, match="AUOREO_SERVER_PASSWORD"):
        TunnelManager(home, None).start("fake", 4096)
    assert not (home / "tunnel.json").exists()


def test_start_refuses_when_binary_missing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("auoreo.core.tunnels.shutil.which", lambda _name: None)
    with pytest.raises(TunnelError, match="developers.cloudflare.com"):
        TunnelManager(home, "pw").start("cloudflared", 4096)
    with pytest.raises(TunnelError, match="tailscale.com/download"):
        TunnelManager(home, "pw").start("tailscale", 4096)
    with pytest.raises(TunnelError, match="unknown tunnel provider"):
        TunnelManager(home, "pw").start("ngrok", 4096)


def test_fake_tunnel_lifecycle(home: Path) -> None:
    mgr = TunnelManager(home, "pw")
    info = mgr.start("fake", 5000)
    assert info.url == FAKE_URL and info.ws_url == "wss://fake-tunnel.trycloudflare.com/ws"
    state = json.loads((home / "tunnel.json").read_text())
    assert state == {"provider": "fake", "pid": None, "url": FAKE_URL, "port": 5000}
    with pytest.raises(TunnelError, match="already running"):
        mgr.start("fake", 5000)
    status = mgr.status()
    assert status is not None and status.port == 5000
    assert mgr.stop() is not None and mgr.status() is None and mgr.stop() is None


def test_cloudflared_url_parsed_from_log(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A stand-in `cloudflared` script prints the banner on stderr like the real one."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "cloudflared"
    script.write_text(
        "#!/bin/sh\necho 'INF |  https://quiet-river-1234.trycloudflare.com  |' >&2\nexec sleep 30\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    mgr = TunnelManager(home, "pw")
    info = mgr.start("cloudflared", 4096, timeout_s=10)
    try:
        assert info.url == "https://quiet-river-1234.trycloudflare.com" and info.pid
    finally:
        mgr.stop()


def test_tunnel_cli(cli_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = runner.invoke(app, ["tunnel", "start", "--provider", "fake"])
    assert r.exit_code == 2 and "AUOREO_SERVER_PASSWORD" in r.output
    monkeypatch.setenv("AUOREO_SERVER_PASSWORD", "pw")
    r = runner.invoke(app, ["tunnel", "start", "--provider", "fake", "--port", "4100"])
    assert r.exit_code == 0, r.output
    assert "wss://fake-tunnel.trycloudflare.com/ws" in r.output
    r = runner.invoke(app, ["tunnel", "status", "--json"])
    data = json.loads(r.output)
    assert data["ws_url"].startswith("wss://") and data["port"] == 4100 and data["alive"]
    r = runner.invoke(app, ["tunnel", "status"])
    assert "wss://fake-tunnel.trycloudflare.com/ws" in r.output
    assert runner.invoke(app, ["tunnel", "stop"]).exit_code == 0
    assert runner.invoke(app, ["tunnel", "status"]).exit_code == 1


# -------------------------------------------------------------------------- CLI
def test_run_formats(cli_env: Path) -> None:
    r = runner.invoke(app, ["run", "hello", "--format", "json"])
    assert r.exit_code == 0, r.output
    result = json.loads(r.stdout)
    assert result["status"] == "completed" and result["text"] == "echo: hello"
    assert result["session_id"].startswith("ses_") and result["run_id"].startswith("run_")
    r = runner.invoke(app, ["run", "hi", "--format", "stream-json"])
    lines = [json.loads(line) for line in r.stdout.splitlines() if line.strip()]
    assert lines and lines[-1]["type"] in {"run.completed", "session.title_updated"}
    assert any(e["type"] == "run.completed" for e in lines)
    r = runner.invoke(app, ["run", "hi", "--json"])  # alias of stream-json
    assert json.loads(r.stdout.splitlines()[0])["type"] == "run.started"
    assert runner.invoke(app, ["run", "hi", "--format", "xml"]).exit_code == 2


def test_session_ops_and_logs_cli(cli_env: Path) -> None:
    sid = json.loads(runner.invoke(app, ["run", "first", "--format", "json"]).stdout)["session_id"]
    r = runner.invoke(app, ["session", "export", sid, "--format", "md"])
    assert r.exit_code == 0 and "## User\n\nfirst" in r.stdout
    exported = json.loads(runner.invoke(app, ["session", "export", sid, "--format", "json"]).stdout)
    assert exported["session"]["id"] == sid and len(exported["messages"]) == 2
    child = runner.invoke(app, ["session", "fork", sid]).stdout.strip()
    assert child.startswith("ses_") and child != sid
    assert runner.invoke(app, ["session", "archive", child]).exit_code == 0
    listed = json.loads(runner.invoke(app, ["session", "list", "--json"]).stdout)
    assert child not in [s["id"] for s in listed]
    listed = json.loads(runner.invoke(app, ["session", "list", "--json", "--archived"]).stdout)
    assert child in [s["id"] for s in listed]
    r = runner.invoke(app, ["logs", "--session", sid])
    assert r.exit_code == 0
    first = r.stdout.splitlines()[0].split(" ", 2)
    assert first[0].isdigit() and first[1] == "session.created"
    assert any(" run.completed " in line for line in r.stdout.splitlines())
    assert runner.invoke(app, ["session", "delete", child]).exit_code == 0
    r = runner.invoke(app, ["session", "delete", child])
    assert r.exit_code == 1 and "unknown session" in r.output
