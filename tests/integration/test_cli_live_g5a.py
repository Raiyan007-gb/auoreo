"""G5a against a real `auoreo serve` subprocess: discovery lines, `run --attach`, session
create/queue/steer/attach/share over HTTP, and the doctor's live-server rows."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.core import diagnostics_cli
from auoreo.core.settings import Settings

runner = CliRunner()


def _serve(workdir: Path, *flags: str) -> tuple[subprocess.Popen[str], int, list[str]]:
    env = {**os.environ, "AUOREO_GATEWAY": "fake"}
    cmd = [sys.executable, "-m", "auoreo.cli.main", "serve", "--port", "0", *flags]
    proc = subprocess.Popen(
        cmd, cwd=workdir, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    assert proc.stdout is not None
    lines: list[str] = []
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        lines.append(line.rstrip("\n"))
        if line.startswith("server listening on "):
            if "--no-ws" not in flags:
                lines.append(proc.stdout.readline().rstrip("\n"))
            port = int(line.rsplit(":", 1)[1])
            return proc, port, lines
    proc.kill()
    raise AssertionError(f"server did not start: {lines}")


def _stop(proc: subprocess.Popen[str]) -> None:
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def server(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    proc, port, lines = _serve(workdir, "--ws-ping-interval", "5", "--ws-ping-timeout", "7")
    assert lines[-2] == f"server listening on http://127.0.0.1:{port}"  # discovery contract, exact
    assert lines[-1] == f"websocket on ws://127.0.0.1:{port}/ws"
    monkeypatch.setenv("AUOREO_PORT", str(port))
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    monkeypatch.chdir(workdir)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        _stop(proc)


def _ok(args: list[str]) -> str:
    r = runner.invoke(app, args)
    assert r.exit_code == 0, (args, r.output)
    return r.stdout


def test_run_attach_and_session_commands_over_http(server: str) -> None:
    first = json.loads(_ok(["run", "hello", "--attach", server, "--format", "json", "--title", "remote"]))
    assert first["status"] == "completed" and first["transport"] == server
    sid = first["session_id"]
    again = json.loads(_ok(["run", "more", "--attach", server, "-c", "--format", "json"]))
    assert again["session_id"] == sid
    printed = _ok(["run", "only text", "--attach", server, "--print"])
    assert printed.strip() and "session" not in printed
    detail = json.loads(_ok(["session", "get", sid, "--json"]))  # discovered via AUOREO_PORT
    assert detail["title"] == "remote"
    created = _ok(["session", "create", "--title", "via-http"]).strip()
    _ok(["session", "rename", created, "renamed-http"])
    assert json.loads(_ok(["session", "get", created, "--json", "--url", server]))["title"] == "renamed-http"
    assert "followup" in _ok(["session", "queue", created, "queued message"])
    assert "steer" in _ok(["session", "steer", created, "steer message"])
    replayed = runner.invoke(app, ["session", "attach", sid, "--replay", "--until-idle", "--json"])
    assert replayed.exit_code == 0, replayed.output
    kinds = [json.loads(line)["type"] for line in replayed.stdout.splitlines() if line.startswith("{")]
    assert kinds[0] == "session.created" and kinds[-1] == "run.completed"
    url = _ok(["session", "share", sid]).strip()
    assert url.startswith(server + "/api/shared/shr_")
    import httpx

    assert httpx.get(url, timeout=10).json()["session"]["id"] == sid
    assert runner.invoke(app, ["session", "attach", "ses_nope"]).exit_code == 1


def test_doctor_live_rows(server: str, home: Path) -> None:
    import asyncio

    st = Settings(home=home, port=int(server.rsplit(":", 1)[1]))
    rows = {c.check: c for c in asyncio.run(diagnostics_cli.cli_checks(st))}
    assert rows["code_sha"].level == "ok" and "runs this code" in rows["code_sha"].detail
    assert rows["ws_upgrade"].level == "ok" and "upgraded" in rows["ws_upgrade"].detail
    assert rows["websockets"].level == "ok"
    assert rows["update"].detail.startswith("skipped") and rows["searxng"].detail.startswith("skipped")
    r = runner.invoke(app, ["doctor", "--json"])
    names = {row["check"] for row in json.loads(r.stdout)}
    assert {"code_sha", "ws_upgrade", "tunnel", "searxng", "gh_auth", "update", "websockets"} <= names


def test_no_ws_server_fails_upgrade(workdir: Path, home: Path) -> None:
    import asyncio

    proc, port, lines = _serve(workdir, "--no-ws")
    try:
        assert not any(line.startswith("websocket on") for line in lines)
        st = Settings(home=home, port=port)
        rows = {c.check: c for c in asyncio.run(diagnostics_cli.cli_checks(st))}
        assert rows["code_sha"].level == "ok" and rows["ws_upgrade"].level == "warn"
    finally:
        _stop(proc)


async def test_doctor_rows_without_server(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    st = Settings(home=home, port=port, searxng_url="http://127.0.0.1:9")
    rows = {c.check: c for c in await diagnostics_cli.cli_checks(st)}
    assert rows["code_sha"].detail.startswith("skipped") and rows["ws_upgrade"].detail.startswith("skipped")
    assert rows["tunnel"].level == "ok" and rows["tunnel"].detail.startswith("none running")
    monkeypatch.delenv("AUOREO_OFFLINE")
    searx = await diagnostics_cli.searxng_row(st)
    assert searx.level == "warn" and "unreachable" in searx.detail
    stale = diagnostics_cli.code_sha_row({"status": "ok", "code_sha": "deadbeef"}, "http://x")
    assert stale.level == "warn" and "restart" in stale.remedy
    old = diagnostics_cli.code_sha_row({"status": "ok"}, "http://x")
    assert old.level == "warn"


def test_gh_row_is_honest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(diagnostics_cli.shutil, "which", lambda _n: None)
    assert diagnostics_cli.gh_row().detail.startswith("skipped")
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\necho 'You are not logged into any GitHub hosts.' >&2\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setattr(diagnostics_cli.shutil, "which", lambda _n: str(fake))
    row = diagnostics_cli.gh_row()
    assert row.level == "warn" and row.remedy == "gh auth login"
