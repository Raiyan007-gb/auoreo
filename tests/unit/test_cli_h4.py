"""H4: CLI surface — --profile, completion, kanban, messaging, mcp, model, agent, command, lsp."""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.cli.profiles import apply_profile

runner = CliRunner()


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, workdir: Path, home: Path) -> Path:
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    monkeypatch.setenv("AUOREO_PORT", "9")  # nothing answers: commands take their offline path
    monkeypatch.setenv("AUOREO_HOME", str(home))  # restored even when --profile rewrites it
    monkeypatch.setenv("AUOREO_PROFILE", "")
    monkeypatch.chdir(workdir)
    return workdir


def _ok(args: list[str], code: int = 0, **kw: object) -> str:
    res = runner.invoke(app, args, **kw)  # type: ignore[arg-type]
    assert res.exit_code == code, (res.exit_code, res.output, res.exception)
    return res.output


def _json(out: str) -> object:
    start = min(i for i in (out.find("{"), out.find("[")) if i >= 0)
    return json.loads(out[start:])


# ------------------------------------------------------------------ --profile
def test_profile_selects_isolated_home(home: Path) -> None:
    out = _ok(["-p", "work", "config", "path"])
    assert out.strip() == str(home / "profiles" / "work" / "settings.json")
    assert (home / "profiles" / "work").is_dir()
    _ok(["--profile", "work", "config", "set", "port", "5000"])
    assert json.loads((home / "profiles" / "work" / "settings.json").read_text())["port"] == 5000
    assert not (home / "settings.json").exists()  # never the shared root


def test_profile_from_env_and_invalid_name(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUOREO_PROFILE", "ops")
    assert _ok(["config", "path"]).strip() == str(home / "profiles" / "ops" / "settings.json")
    monkeypatch.setenv("AUOREO_PROFILE", "")
    monkeypatch.setenv("AUOREO_HOME", str(home))
    assert "invalid profile name" in _ok(["-p", "Bad/Name", "config", "path"], 2)


def test_apply_profile_is_idempotent(home: Path) -> None:
    first = apply_profile("x")
    assert first == home / "profiles" / "x" and os.environ["AUOREO_HOME"] == str(first)
    assert apply_profile("x") == first
    assert apply_profile(None) is None


# ------------------------------------------------------------------ completion
@pytest.mark.parametrize("shell", ["bash", "zsh", "fish", "pwsh", "powershell"])
def test_completion_scripts(shell: str) -> None:
    out = _ok(["completion", shell])
    assert "_AUOREO_COMPLETE" in out and "auoreo" in out
    assert "auoreo completion" in _ok(["completion", shell, "--hint"])


def test_completion_unknown_shell() -> None:
    assert "unsupported shell" in _ok(["completion", "tcsh"], 2)


# ------------------------------------------------------------------ kanban
def test_kanban_lifecycle() -> None:
    assert "created #1 [ready] ops: write docs" in _ok(
        ["kanban", "add", "write docs", "-b", "ops", "--body", "b"]
    )
    _ok(["kanban", "add", "second"])
    rows = _json(_ok(["kanban", "list", "--json", "--board", "ops"]))
    assert [r["title"] for r in rows] == ["write docs"]  # type: ignore[index]
    assert "[blocked]" in _ok(["kanban", "block", "1", "needs review"])
    assert "[ready]" in _ok(["kanban", "move", "1", "ready", "--note", "unblocked"])
    assert "moved #1 [ready] later" in _ok(["kanban", "move", "1", "--board", "later"])
    assert "[done]" in _ok(["kanban", "done", "1", "-m", "shipped"])
    shown = _json(_ok(["kanban", "show", "1", "--json"]))
    texts = [c["text"] for c in shown["comments"]]  # type: ignore[index]
    assert texts[0].endswith("needs review") and "shipped" in texts and shown["board"] == "later"  # type: ignore[index]
    assert len(_json(_ok(["kanban", "list", "--json", "--status", "done"]))) == 1  # type: ignore[arg-type]


def test_kanban_errors() -> None:
    assert "unknown kanban task 42" in _ok(["kanban", "show", "42"], 1)
    _ok(["kanban", "add", "t"])
    assert "cannot move to 'claimed'" in _ok(["kanban", "move", "1", "claimed"], 1)
    assert "nothing to move" in _ok(["kanban", "move", "1"], 1)
    _ok(["kanban", "list", "--status", "bogus"], 2)


# ------------------------------------------------------------------ messaging
def test_messaging_setup_status_start(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    status = _json(_ok(["messaging", "telegram", "status", "--json"]))
    assert status["status"] == "disabled" and status["secrets"] == {"telegram/bot_token": False}  # type: ignore[index]
    out = _ok(["messaging", "telegram", "setup"], 1)  # enabled, but no credential yet
    assert "auoreo secrets set telegram/bot_token" in out
    manifest = yaml.safe_load((home / "plugins" / "telegram" / "plugin.yaml").read_text())
    assert manifest["enabled"] is True
    monkeypatch.setenv("telegram/bot_token", "123:abc-not-printed")
    out = _ok(["messaging", "telegram", "setup", "--json"])
    assert "abc-not-printed" not in out and _json(out)["secrets"] == {"telegram/bot_token": True}  # type: ignore[index]
    assert _json(_ok(["messaging", "telegram", "status", "--json"]))["status"] == "stopped"  # type: ignore[index]
    assert "no server answered" in _ok(["messaging", "telegram", "start"], 1)


def test_messaging_pair_and_errors(home: Path) -> None:
    from auoreo.plugins.connectors import PairingStore

    code = PairingStore(home, "telegram").request("777", "alice")
    assert "paired chat 777" in _ok(["messaging", "telegram", "pair", code])
    assert json.loads((home / "settings.json").read_text())["plugins"]["telegram"]["allowlist"] == ["777"]
    assert "unknown or expired" in _ok(["messaging", "telegram", "pair", "000000"], 1)
    dcode = PairingStore(home, "discord").request("4242", "bob")  # I7: discord pairs like telegram
    assert "paired chat 4242" in _ok(["messaging", "discord", "pair", dcode])
    assert "usage: auoreo messaging discord pair" in _ok(["messaging", "discord", "pair"], 2)
    assert "unknown platform" in _ok(["messaging", "irc", "status"], 2)
    assert "unknown action" in _ok(["messaging", "discord", "launch"], 2)


# ------------------------------------------------------------------ mcp
SERVER = """
from mcp.server import MCPServer
srv = MCPServer("demo")

@srv.tool()
def ping(text: str) -> str:
    return "pong " + text

srv.run()
"""


def test_mcp_probe_connects_to_a_real_stdio_server(tmp_path: Path) -> None:
    from auoreo.cli.commands import mcp as mcp_cli
    from auoreo.harness.manifests import McpServerManifest

    script = tmp_path / "srv.py"
    script.write_text(textwrap.dedent(SERVER))
    m = McpServerManifest(name="demo", command=sys.executable, args=[str(script)])
    ok, detail, names = mcp_cli.probe(m, 30)
    assert (ok, detail, names) == (True, "connected, 1 tool(s)", ["mcp__demo__ping"])
    bad = McpServerManifest(name="bad", command="/nonexistent/server-bin")
    ok, detail, _ = mcp_cli.probe(bad, 10)
    assert not ok and "No such file" in detail


def test_mcp_add_test_rm(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # CliRunner swaps stderr for an in-memory buffer, which a stdio child cannot inherit;
    # the live connection is covered by the probe test above.
    from auoreo.cli.commands import mcp as mcp_cli

    seen: list[str] = []

    def fake_probe(m: object, timeout: float) -> tuple[bool, str, list[str]]:
        seen.append(m.name)  # type: ignore[attr-defined]
        return True, "connected, 1 tool(s)", ["mcp__demo__ping"]

    monkeypatch.setattr(mcp_cli, "probe", fake_probe)
    script = tmp_path / "srv.py"
    out = _ok(["mcp", "add", "demo", "--", sys.executable, str(script)])
    assert "check: connected, 1 tool(s)" in out and seen == ["demo"]
    doc = yaml.safe_load((home / "mcp" / "demo.yaml").read_text())
    assert doc["command"] == sys.executable and doc["args"] == [str(script)]
    rows = _json(_ok(["mcp", "list", "--json"]))
    assert {r["name"]: r["source"] for r in rows}["demo"] == "runtime"  # type: ignore[index, union-attr]
    tested = _json(_ok(["mcp", "test", "demo", "--json"]))
    assert tested["ok"] and tested["tools"] == ["mcp__demo__ping"]  # type: ignore[index]
    assert "exists" in _ok(["mcp", "add", "demo", "--no-check", "--url", "http://127.0.0.1:1/mcp"], 1)
    assert "removed" in _ok(["mcp", "rm", "demo"])
    assert not (home / "mcp" / "demo.yaml").exists()


def test_mcp_add_refusals(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from auoreo.cli.commands import mcp as mcp_cli

    monkeypatch.setattr(mcp_cli, "probe", lambda m, t: (False, "error: [Errno 2] No such file", []))
    out = _ok(["mcp", "add", "bad", "--", "/nonexistent/server-bin"], 1)
    assert "connection check failed" in out and not (home / "mcp" / "bad.yaml").exists()
    secretish = "ghp_" + "a1B2" * 9
    out = _ok(["mcp", "add", "gh", "--no-check", "--env", f"TOKEN={secretish}", "--", "x"], 2)
    assert "secret:<name>" in out
    _ok(["mcp", "add", "gh", "--no-check", "--env", "TOKEN=secret:github/token", "--", "x"])
    assert yaml.safe_load((home / "mcp" / "gh.yaml").read_text())["env"] == {"TOKEN": "secret:github/token"}
    _ok(["mcp", "add", "both", "--url", "http://x", "--", "cmd"], 2)
    assert "bundled root" in _ok(["mcp", "rm", "fetch"], 1)
    assert "unknown MCP server" in _ok(["mcp", "test", "nope"], 1)


# ------------------------------------------------------------------ model
def test_model_list_set_test(home: Path) -> None:
    listing = _json(_ok(["model", "list", "--json"]))
    assert listing["gateway"] == "fake" and {r["group"] for r in listing["models"]} >= {"default", "small"}  # type: ignore[index]
    _ok(["model", "set", "anthropic/claude-sonnet-5"])
    _ok(["model", "set", "small", "--small"])
    data = json.loads((home / "settings.json").read_text())
    assert data["model"] == "anthropic/claude-sonnet-5" and data["small_model"] == "small"
    _ok(["model", "set", "nogroup"], 2)
    tested = _json(_ok(["model", "test", "default", "--json"]))
    assert tested["ok"] and tested["text"].startswith("echo:")  # type: ignore[index]


# ------------------------------------------------------------------ agent / command
def test_agent_list_show_new(home: Path, workdir: Path) -> None:
    names = [r["name"] for r in _json(_ok(["agent", "list", "--json"]))]  # type: ignore[index, union-attr]
    assert "default" in names
    assert "You are the default AUOREO agent" in _ok(["agent", "show", "default"])
    _ok(["agent", "show", "ghost"], 1)
    _ok(["agent", "new", "triage", "-d", "Sorts incoming issues.", "-m", "small", "--tool", "read"])
    text = (home / "agents" / "triage" / "AGENT.md").read_text()
    assert "description: Sorts incoming issues." in text and "- read" in text
    assert _json(_ok(["agent", "show", "triage", "--json"]))["source"] == "runtime"  # type: ignore[index]
    assert "exists" in _ok(["agent", "new", "triage"], 1)
    _ok(["agent", "new", "local_one", "--project"])
    assert (workdir / ".auoreo" / "agents" / "local_one" / "AGENT.md").is_file()
    _ok(["agent", "new", "Bad-Name"], 1)
    assert not (home / "agents" / "Bad-Name").exists()


def test_command_list_show() -> None:
    rows = _json(_ok(["command", "list", "--json"]))
    assert "/review" in [r["name"] for r in rows]  # type: ignore[index, union-attr]
    assert "{{args}}" in _ok(["command", "show", "/review"])
    _ok(["command", "show", "nope"], 1)


# ------------------------------------------------------------------ lsp
def test_lsp_status_start_stop(home: Path) -> None:
    rows = _json(_ok(["lsp", "status", "--json"]))
    assert {r["language"] for r in rows} >= {"python", "typescript"}  # type: ignore[union-attr, index]
    (home / "settings.json").write_text(json.dumps({"lsp_servers": {"python": ["/nonexistent/lsp-bin"]}}))
    rows = _json(_ok(["lsp", "status", "--json"]))
    assert next(r for r in rows if r["language"] == "python")["source"] == "settings"  # type: ignore[union-attr, index]
    assert "cannot start /nonexistent/lsp-bin" in _ok(["lsp", "start", "python"], 1)
    _ok(["lsp", "start", "cobol"], 2)
    assert "no running server" in _ok(["lsp", "stop"], 1)  # I5: POST /api/lsp/stop when one runs
