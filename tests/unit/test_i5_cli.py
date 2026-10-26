"""I5 CLI: global --json/--mini, reload subcommands, plugin get/set/install, pairing, queue,
lsp stop, logs --level, serve/web LAN lines and the --ws-auth help."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from auoreo.cli import output, target
from auoreo.cli.app import app
from auoreo.cli.serve_extras import lan_lines
from auoreo.core.event_levels import at_least, level_of
from auoreo.harness.manifests import PluginManifest
from auoreo.plugins import config_values as cv
from auoreo.plugins.connectors import PairingStore

runner = CliRunner()


@pytest.fixture(autouse=True)
def _reset_output() -> Iterator[None]:
    yield
    output.reset()


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, workdir: Path) -> Path:
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    monkeypatch.setenv("AUOREO_PORT", "9")  # nothing listens: commands fall back to disk
    monkeypatch.chdir(workdir)
    return workdir


# ------------------------------------------------------------------ global flags
def test_global_json_and_mini(cli_env: Path) -> None:
    r = runner.invoke(app, ["--json", "version"])
    assert r.exit_code == 0 and "version" in json.loads(r.stdout)
    r = runner.invoke(app, ["--json", "agent", "list"])
    assert r.exit_code == 0, r.output
    agents = json.loads(r.stdout)
    assert any(a["name"] == "default" for a in agents)
    r = runner.invoke(app, ["--mini", "--json", "command", "list"])
    assert r.exit_code == 0 and len(r.stdout.strip().splitlines()) == 1
    assert isinstance(json.loads(r.stdout), list)
    plain = runner.invoke(app, ["agent", "list"])
    assert plain.exit_code == 0 and not plain.stdout.lstrip().startswith("[")  # flags do not leak


def test_json_defaults_cover_nested_groups() -> None:
    import typer.main

    cmd = typer.main.get_command(app)
    defaults = output.json_defaults(cmd)
    assert defaults["session"]["list"] == {"as_json": True}
    assert defaults["doctor"] == {"as_json": True} and "version" not in defaults


# ------------------------------------------------------------------ reload
def test_reload_subcommands(cli_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(target, "discover", lambda url=None, st=None: None)
    r = runner.invoke(app, ["agent", "reload"])
    assert r.exit_code == 1 and "no running server" in r.output
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_call(base: str, method: str, path: str, **kw: Any) -> dict[str, Any]:
        calls.append((method, path, kw))
        return {
            "generation": 7,
            "errors": [],
            "changes": {"agents": {"added": ["new-one"], "removed": []}},
            "kind": kw["params"]["kind"],
            "note": None,
        }

    monkeypatch.setattr(target, "discover", lambda url=None, st=None: "http://srv")
    monkeypatch.setattr(target, "call", fake_call)
    for group, kind in [("command", "command"), ("agent", "agent"), ("model", "model")]:
        r = runner.invoke(app, [group, "reload"])
        assert r.exit_code == 0 and "generation 7" in r.output, r.output
        assert calls[-1] == ("POST", "/api/harness/reload", {"params": {"kind": kind}})
    assert "agents added: new-one" in runner.invoke(app, ["agent", "reload"]).output
    for group in ("config", "lsp", "mcp"):
        assert json.loads(runner.invoke(app, [group, "reload", "--json"]).stdout)["kind"] == group


def test_lsp_stop_calls_the_server(cli_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(target, "discover", lambda url=None, st=None: None)
    assert runner.invoke(app, ["lsp", "stop"]).exit_code == 1
    seen: dict[str, Any] = {}

    def fake_call(base: str, method: str, path: str, **kw: Any) -> dict[str, Any]:
        seen.update(path=path, body=kw["json"])
        return {"stopped": ["s1:python:/w"], "count": 1}

    monkeypatch.setattr(target, "discover", lambda url=None, st=None: "http://srv")
    monkeypatch.setattr(target, "call", fake_call)
    r = runner.invoke(app, ["lsp", "stop", "--language", "python"])
    assert r.exit_code == 0 and "1 language server(s) stopped" in r.output
    assert seen == {"path": "/api/lsp/stop", "body": {"language": "python"}}


# ------------------------------------------------------------------ plugin get/set
def test_plugin_get_set_refuses_secrets(cli_env: Path, home: Path) -> None:
    r = runner.invoke(app, ["plugin", "set", "telegram", "poll_timeout_s", "10"])
    assert r.exit_code == 0, r.output
    assert runner.invoke(app, ["plugin", "set", "telegram", "allowlist", '["42"]']).exit_code == 0
    cfg = json.loads((home / "settings.json").read_text())["plugins"]["telegram"]
    assert cfg == {"poll_timeout_s": 10, "allowlist": ["42"]}
    r = runner.invoke(app, ["plugin", "set", "telegram", "bot_token", "123:abc"])
    assert r.exit_code == 2 and "secret" in r.output
    assert "123:abc" not in (home / "settings.json").read_text()
    r = runner.invoke(app, ["plugin", "set", "telegram", "poll_timeout_s", "99"])
    assert r.exit_code == 2 and "maximum" in r.output.lower()
    r = runner.invoke(app, ["plugin", "set", "telegram", "nope", "1"])
    assert r.exit_code == 2 and "no config key" in r.output
    r = runner.invoke(app, ["plugin", "get", "telegram", "--json"])
    assert json.loads(r.stdout) == {"allowlist": ["42"], "poll_timeout_s": 10}
    assert runner.invoke(app, ["plugin", "get", "nosuch"]).exit_code == 1
    assert "Alias of `plugin add`" in runner.invoke(app, ["plugin", "install", "--help"]).output


def test_secret_detection_from_schema(home: Path) -> None:
    m = PluginManifest.model_validate(
        {
            "name": "demo",
            "version": "1.0.0",
            "description": "d",
            "provides": ["tools"],
            "config_schema": {
                "type": "object",
                "properties": {"pin": {"type": "string", "format": "password"}, "region": {"type": "string"}},
            },
        }
    )
    assert cv.is_secret(m, "pin") and cv.is_secret(m, "api_key") and not cv.is_secret(m, "region")
    (home / "settings.json").write_text(json.dumps({"plugins": {"demo": {"pin": "1234", "region": "eu"}}}))
    assert cv.values(home, {}, m) == {"pin": cv.MASK, "region": "eu"}
    with pytest.raises(cv.PluginConfigError, match="secret"):
        cv.set_value(home, m, "pin", "9999")


# ------------------------------------------------------------------ pairing + queue
def test_pairing_list_approve_revoke(cli_env: Path, home: Path) -> None:
    code = PairingStore(home, "telegram").request("777", "alice")
    listed = json.loads(runner.invoke(app, ["pairing", "list", "--json"]).stdout)
    assert listed == [
        {"platform": "telegram", "allowlist": [], "pending": [{"chat_id": "777", "user": "alice"}]}
    ]
    assert code not in json.dumps(listed)  # codes are never printed
    r = runner.invoke(app, ["pairing", "approve", "telegram", code])
    assert r.exit_code == 0 and "paired chat 777" in r.output, r.output
    assert runner.invoke(app, ["pairing", "approve", "telegram", code]).exit_code == 1  # consumed
    assert runner.invoke(app, ["pairing", "revoke", "telegram", "777"]).exit_code == 0
    assert runner.invoke(app, ["pairing", "revoke", "telegram", "777"]).exit_code == 1
    assert runner.invoke(app, ["pairing", "list", "discord"]).exit_code == 2  # no inbound connector


def test_top_level_queue_is_session_queue(cli_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}
    monkeypatch.setattr(target, "require", lambda url=None, st=None: "http://srv")
    monkeypatch.setattr(target, "call", lambda b, m, p, **kw: sent.update(kw["json"]) or {"run_id": "run_1"})
    r = runner.invoke(app, ["queue", "ses_1", "next step"])
    assert r.exit_code == 0 and "run_1" in r.output
    assert sent == {"message": "next step", "session_id": "ses_1", "mode": "followup"}


# ------------------------------------------------------------------ logs --level
def test_event_levels() -> None:
    assert level_of("message.delta") == "debug" and level_of("run.failed") == "error"
    assert level_of("plugin.crashed") == "error" and level_of("tool.denied") == "warn"
    assert level_of("tool.completed", {"is_error": True}) == "warn" and level_of("session.created") == "info"
    assert at_least("run.blocked", None, "warn") and not at_least("message.delta", None, "info")


def test_logs_level_filters(cli_env: Path) -> None:
    assert runner.invoke(app, ["run", "hello", "--format", "json"]).exit_code == 0
    every = runner.invoke(app, ["logs", "--json"]).stdout.splitlines()
    assert {json.loads(x)["level"] for x in every} >= {"info"}
    warn = runner.invoke(app, ["logs", "--level", "warn"]).stdout
    assert "run.completed" not in warn and "session.created" not in warn
    info = runner.invoke(app, ["logs", "--level", "info"]).stdout
    assert "run.completed" in info and "message.delta" not in info
    assert runner.invoke(app, ["logs", "--level", "loud"]).exit_code == 2


# ------------------------------------------------------------------ serve/web
def test_lan_lines() -> None:
    assert lan_lines("127.0.0.1", 4096) == [] and lan_lines("::1", 1) == []
    assert lan_lines("0.0.0.0", 4096, ["192.168.1.5", "10.0.0.2"]) == [
        "lan http://192.168.1.5:4096",
        "lan http://10.0.0.2:4096",
    ]
    assert lan_lines("192.168.1.9", 80) == ["lan http://192.168.1.9:80"]
    assert lan_lines("fe80::1", 80) == ["lan http://[fe80::1]:80"]


def test_ws_auth_help_lists_every_mode() -> None:
    r = runner.invoke(app, ["serve", "--help"], env={"COLUMNS": "200"})
    flat = " ".join(r.output.split())
    for mode in ("token", "jwt", "capability", "off", "spec enum"):
        assert mode in flat, mode
