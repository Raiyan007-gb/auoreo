"""G5a CLI: serve flag resolution, session (in-process), debug bundle/snapshot, db inspect,
webhook add/deliveries/replay, secrets rotate/export, exec/apply/review, stats --project."""

from __future__ import annotations

import json
import os
import socket
import stat
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.cli import serve_extras
from auoreo.cli.app import app
from auoreo.core.secrets import SecretStore
from auoreo.core.settings import Settings

runner = CliRunner()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, workdir: Path) -> Path:
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    monkeypatch.setenv("AUOREO_PORT", str(_free_port()))  # never discover someone else's server
    monkeypatch.chdir(workdir)
    return workdir


def _ok(args: list[str], **kw: object) -> str:
    r = runner.invoke(app, args, **kw)  # type: ignore[arg-type]
    assert r.exit_code == 0, (args, r.output)
    return r.stdout


# ------------------------------------------------------------------ serve flags
def test_serve_extras_resolve_applies_flags(home: Path) -> None:
    st = Settings(home=home)
    ex = serve_extras.resolve(
        st,
        ws_ping_interval=5,
        ws_ping_timeout=9,
        cors=["https://app.example/"],
        trusted_hosts=["box.lan"],
        ws_auth="off",
    )
    assert st.ws_ping_interval_s == 5 and st.cli.ws_ping_timeout_s == 9
    assert "https://app.example" in st.cors_origins and "box.lan" in st.http.trusted_hosts
    assert st.cli.ws_auth == "off"
    assert ex.uvicorn_kwargs(st) == {"ws_ping_interval": 5, "ws_ping_timeout": 9}
    assert ex.ws_line("127.0.0.1", 4100) == "websocket on ws://127.0.0.1:4100/ws"
    assert ex.ws_line("::1", 4100) == "websocket on ws://[::1]:4100/ws"
    off = serve_extras.resolve(Settings(home=home), ws=False)
    assert off.uvicorn_kwargs(st) == {"ws": "none"} and off.ws_line("127.0.0.1", 1) is None


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"ws_auth": "jwt"}, "not implemented"),
        ({"ws_auth": "capability"}, "not implemented"),
        ({"ws_auth": "bogus"}, "must be one of"),
        ({"tunnel": "ngrok"}, "must be one of"),
        ({"tunnel": "fake"}, "AUOREO_SERVER_PASSWORD"),
        ({"ws_ping_interval": 0}, "must be > 0"),
    ],
)
def test_serve_extras_refusals(home: Path, kwargs: dict, match: str) -> None:  # type: ignore[type-arg]
    with pytest.raises(serve_extras.ServeOptionError, match=match):
        serve_extras.resolve(Settings(home=home), **kwargs)


def test_serve_ws_auth_off_needs_loopback(home: Path) -> None:
    with pytest.raises(serve_extras.ServeOptionError, match="loopback"):
        serve_extras.resolve(Settings(home=home, host="0.0.0.0", server_password="pw"), ws_auth="off")
    with pytest.raises(serve_extras.ServeOptionError, match="tunnel"):
        serve_extras.resolve(Settings(home=home, server_password="pw"), ws_auth="off", tunnel="fake")


def test_serve_mdns_is_honest_about_zeroconf(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real = builtins.__import__

    def no_zeroconf(name: str, *a: object, **kw: object) -> object:
        if name.startswith("zeroconf"):
            raise ImportError("no zeroconf")
        return real(name, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_zeroconf)
    st = Settings(home=home, server_password="pw")
    with pytest.raises(serve_extras.ServeOptionError, match="zeroconf"):
        serve_extras.resolve(st, mdns=True)
    assert st.host == "0.0.0.0"  # --mdns binds the LAN


def test_serve_cli_refuses_bad_flags(cli_env: Path) -> None:
    r = runner.invoke(app, ["serve", "--port", "0", "--ws-auth", "jwt"])
    assert r.exit_code == 2 and "not implemented" in r.output
    r = runner.invoke(app, ["serve", "--port", "0", "--mdns"])  # mdns → 0.0.0.0 without a password
    assert r.exit_code == 2


async def test_serve_extras_fake_tunnel_lifecycle(home: Path) -> None:
    st = Settings(home=home, server_password="pw")
    ex = serve_extras.resolve(st, tunnel="fake")
    lines: list[str] = []
    await ex.started(st, 4100, lines.append)
    assert any(line.startswith("tunnel https://") for line in lines)
    assert any("wss://" in line for line in lines)
    assert (home / "tunnel.json").is_file()
    await ex.stopping(st)
    assert not (home / "tunnel.json").exists()


# ------------------------------------------------------------------ session (in-process)
def test_session_commands_in_process(cli_env: Path) -> None:
    sid = _ok(["session", "create", "--title", "first"]).strip()
    assert sid.startswith("ses_")
    detail = json.loads(_ok(["session", "get", sid, "--json"]))
    assert detail["title"] == "first" and detail["archived"] is False
    _ok(["session", "rename", sid, "renamed"])
    assert json.loads(_ok(["session", "get", sid, "--json"]))["title"] == "renamed"
    _ok(["session", "archive", sid])
    _ok(["session", "unarchive", sid])
    assert json.loads(_ok(["session", "get", sid, "--json"]))["archived"] is False
    _ok(["run", "hello", "--session", sid, "--format", "json"])
    url = _ok(["session", "share", sid]).strip()
    assert "/api/shared/shr_" in url
    assert "revoked 1" in _ok(["session", "share", sid, "--revoke"])
    assert "retried" in _ok(["session", "retry", sid])
    assert "2 message(s) cut" in _ok(["session", "revert", sid, "--", "-1"])
    assert runner.invoke(app, ["session", "get", "ses_nope"]).exit_code == 1
    assert runner.invoke(app, ["session", "retry", sid]).exit_code == 2  # nothing left to retry


def test_live_only_session_commands_need_a_server(cli_env: Path) -> None:
    for args in (
        ["session", "queue", "ses_x", "hi"],
        ["session", "steer", "ses_x", "hi"],
        ["session", "attach", "ses_x"],
    ):
        r = runner.invoke(app, args)
        assert r.exit_code == 1 and "no running server" in r.output


# ------------------------------------------------------------------ debug + db
def test_debug_bundle_is_redacted(cli_env: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret_value = "zz-unshaped-secret-value-123"
    SecretStore(home, "allow-file").set("custom/thing", secret_value)
    monkeypatch.setenv("AUOREO_SERVER_PASSWORD", "hunter2-password-xyz")
    monkeypatch.setenv("MY_SERVICE_TOKEN", "tok-abcdef-987654")
    (home / "settings.json").write_text(json.dumps({"my_token": "lk-secret-555555"}))
    (home / "tunnel.log").write_text(
        f"line with {secret_value}\nsk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345\n"
    )
    _ok(["run", "hello there", "--format", "json"])
    out = cli_env / "b.zip"
    _ok(["debug", "bundle", "--out", str(out)])
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        blob = "\n".join(zf.read(n).decode() for n in names)
    assert {
        "manifest.json",
        "versions.json",
        "settings.json",
        "doctor.json",
        "db.json",
        "events.json",
    } <= names
    assert "logs/tunnel.log" in names
    for leaked in (
        secret_value,
        "hunter2-password-xyz",
        "tok-abcdef-987654",
        "lk-secret-555555",
        "ABCDEFGHIJKLMNOP",
    ):
        assert leaked not in blob, leaked
    db = json.loads(zipfile.ZipFile(out).read("db.json"))
    assert db["tables"]["sessions"] == 1 and "messages" in db["tables"]


def test_debug_snapshot(cli_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_API_KEY", "plain-looking-value-42")
    sid = json.loads(_ok(["run", "remember plain-looking-value-42", "--format", "json"]))["session_id"]
    text = _ok(["debug", "snapshot", sid, "--out", "-"])
    assert sid in text and "plain-looking-value-42" not in text
    _ok(["debug", "snapshot", sid, "--format", "md"])
    path = cli_env / f"snapshot-{sid}.md"
    assert path.is_file() and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert runner.invoke(app, ["debug", "snapshot", "ses_nope"]).exit_code == 1
    assert runner.invoke(app, ["debug", "snapshot", sid, "--format", "xml"]).exit_code == 2


def test_db_inspect(cli_env: Path) -> None:
    missing = json.loads(_ok(["db", "inspect", "--json"]))
    assert missing["exists"] is False and missing["alembic_head"]
    _ok(["run", "hello", "--format", "json"])
    info = json.loads(_ok(["db", "inspect", "--json"]))
    assert info["schema_version"] == info["code_schema_version"]
    assert info["alembic_current"] == info["alembic_head"]
    assert info["tables"]["sessions"] == 1 and info["files"]["state.db"] > 0
    assert "table" in _ok(["db", "inspect"])


# ------------------------------------------------------------------ webhooks + secrets
def test_webhook_add_deliveries_replay(cli_env: Path, home: Path) -> None:
    r = runner.invoke(app, ["webhook", "add", "ci-hook", "--provider", "github", "--event", "push"])
    assert r.exit_code == 0, r.output
    secret = r.stdout.strip()
    route = cli_env / ".auoreo" / "webhooks" / "ci-hook.yaml"
    text = route.read_text()
    assert "secret:webhook/ci-hook" in text and secret not in text
    assert SecretStore(home, "allow-file").get("webhook/ci-hook") == secret
    assert runner.invoke(app, ["webhook", "add", "ci-hook"]).exit_code == 1  # exists
    assert runner.invoke(app, ["webhook", "add", "Bad Name"]).exit_code == 2
    listed = json.loads(_ok(["webhook", "list", "--json"]))
    assert any(w["name"] == "ci-hook" and w["secret_configured"] for w in listed)
    payload = cli_env / "p.json"
    payload.write_text(json.dumps({"ref": "refs/heads/main"}))
    _ok(["webhook", "test", "ci-hook", "-f", str(payload), "--event", "push"])
    rows = json.loads(_ok(["webhook", "deliveries", "ci-hook", "--json"]))
    assert len(rows) == 1 and rows[0]["source"] == "ci-hook"
    assert json.loads(_ok(["webhook", "deliveries", "other", "--json"])) == []
    prompt = _ok(["webhook", "replay", str(rows[0]["id"])])
    assert "ci-hook" in prompt
    assert runner.invoke(app, ["webhook", "replay", "999"]).exit_code == 1


def test_secrets_rotate_and_export(cli_env: Path, home: Path) -> None:
    store = SecretStore(home, "allow-file")
    store.set("svc/token", "old-value-111111")
    r = runner.invoke(app, ["secrets", "rotate", "svc/token", "--generate"])
    assert r.exit_code == 0 and "old-value" not in r.output
    fresh = store.get("svc/token")
    assert fresh and fresh != "old-value-111111" and fresh not in r.output
    assert store.get("svc/token#prev") == "old-value-111111"
    r = runner.invoke(app, ["secrets", "rotate", "svc/other"], input="typed-value-222222\n")
    assert r.exit_code == 0 and store.get("svc/other") == "typed-value-222222"
    refused = runner.invoke(app, ["secrets", "export"])
    assert refused.exit_code == 2 and fresh not in refused.output
    out = cli_env / "s.json"
    r = runner.invoke(app, ["secrets", "export", "--yes", "--out", str(out)])
    assert r.exit_code == 0 and fresh not in r.output
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert json.loads(out.read_text())["svc/token"] == fresh
    printed = runner.invoke(app, ["secrets", "export", "--stdout", "--yes"])
    assert json.loads(printed.stdout)["svc/other"] == "typed-value-222222"


# ------------------------------------------------------------------ exec / apply / review / stats
def test_exec_runs_through_sandbox_with_floors(cli_env: Path) -> None:
    r = runner.invoke(app, ["exec", "echo hi-from-exec && pwd"])
    assert r.exit_code == 0, r.output
    assert "hi-from-exec" in r.stdout and str(cli_env) in r.stdout
    assert runner.invoke(app, ["exec", "--", "sh", "-c", "exit 3"]).exit_code == 3
    refused = runner.invoke(app, ["exec", "rm -rf /"])
    assert refused.exit_code == 2 and "floor" in refused.output
    assert runner.invoke(app, ["exec", "--via", "bogus", "true"]).exit_code == 2


def test_exec_does_not_leak_secret_env(cli_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak-000")
    r = runner.invoke(app, ["exec", "env"])
    assert r.exit_code == 0 and "sk-should-not-leak" not in r.stdout


def test_exec_via_environment(cli_env: Path) -> None:
    r = runner.invoke(app, ["exec", "--via", "environment", "echo env-backend-ok"])
    assert r.exit_code == 0, r.output
    assert "env-backend-ok" in r.stdout


def test_apply_patch(cli_env: Path) -> None:
    (cli_env / "a.txt").write_text("one\ntwo\n")
    patch = cli_env / "p.patch"
    patch.write_text("*** Begin Patch\n*** Update File: a.txt\n@@\n one\n-two\n+TWO\n*** End Patch\n")
    dry = runner.invoke(app, ["apply", str(patch), "--dry-run"])
    assert dry.exit_code == 0 and "dry run OK" in dry.stdout
    assert (cli_env / "a.txt").read_text() == "one\ntwo\n"
    _ok(["apply", str(patch)])
    assert (cli_env / "a.txt").read_text() == "one\nTWO\n"
    bad = cli_env / "bad.patch"
    bad.write_text("*** Begin Patch\n*** Update File: .env\n@@\n-x\n+y\n*** End Patch\n")
    assert runner.invoke(app, ["apply", str(bad)]).exit_code == 1


def test_review_runs_review_command(cli_env: Path) -> None:
    assert runner.invoke(app, ["review"]).exit_code == 2
    r = runner.invoke(app, ["review", "src/app.py", "--print"])
    assert r.exit_code == 0, r.output
    assert "review" in r.stdout.lower() or r.stdout.strip()


def test_stats_by_project(cli_env: Path) -> None:
    _ok(["run", "hello", "--format", "json"])
    data = json.loads(_ok(["stats", "--project", "--json"]))
    rows = data["by_project"]
    assert rows and rows[0]["key"] == str(cli_env.resolve()) and rows[0]["sessions"] == 1
    assert "usage by project" in _ok(["stats", "--project"])


def test_target_local_url(home: Path) -> None:
    from auoreo.cli.target import local_url

    assert local_url(Settings(home=home, host="0.0.0.0", port=5)) == "http://127.0.0.1:5"
    assert local_url(Settings(home=home, host="::1", port=5)) == "http://[::1]:5"
    assert os.environ.get("AUOREO_SERVER_PASSWORD") is None
