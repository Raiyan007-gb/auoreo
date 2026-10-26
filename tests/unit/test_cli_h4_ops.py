"""H4: loop/heartbeat, config edit, login, new-feature/worktree gc, eval memory,
upgrade --target, debug upload, run --command/--share."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from auoreo import __version__
from auoreo.cli.app import app
from auoreo.cli.loop_support import is_quiet, parse_interval
from auoreo.core import oauth_login as oauth

runner = CliRunner()


@pytest.fixture(autouse=True)
def cli_env(monkeypatch: pytest.MonkeyPatch, workdir: Path, home: Path) -> Path:
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    monkeypatch.setenv("AUOREO_PORT", "9")
    monkeypatch.setenv("AUOREO_HOME", str(home))
    monkeypatch.setenv("AUOREO_PROFILE", "")
    monkeypatch.chdir(workdir)
    return workdir


def _run(args: list[str], code: int = 0, **kw: Any) -> str:
    res = runner.invoke(app, args, **kw)
    assert res.exit_code == code, (res.exit_code, res.output, res.exception)
    return res.output


# ------------------------------------------------------------------ loop / heartbeat
def test_parse_interval_and_quiet_token() -> None:
    assert parse_interval("90") == 90 and parse_interval("5m") == 300 and parse_interval("1h30m") == 5400
    assert parse_interval("0.5s") == 0.5
    for bad in ("", "5x", "m5", "1h junk"):
        with pytest.raises(ValueError):
            parse_interval(bad)
    assert is_quiet(" HEARTBEAT_OK\n", "HEARTBEAT_OK") and is_quiet("`heartbeat_ok`.", "HEARTBEAT_OK")
    assert not is_quiet("HEARTBEAT_OK but the disk is full", "HEARTBEAT_OK")


def test_loop_repeats_in_one_session() -> None:
    out = _run(["loop", "0s", "ping", "-n", "3", "--format", "json"])
    rows = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert [r["iteration"] for r in rows] == [1, 2, 3]
    assert {r["text"] for r in rows} == {"echo: ping"} and len({r["session_id"] for r in rows}) == 1


def test_loop_fresh_sessions_and_bad_interval() -> None:
    out = _run(["loop", "0", "hi", "-n", "2", "--fresh", "--format", "json"])
    rows = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    assert len({r["session_id"] for r in rows}) == 2
    _run(["loop", "soon", "hi"], 2)
    _run(["loop", "1s", "hi", "--format", "xml"], 2)


def test_heartbeat_once_reads_checklist(workdir: Path) -> None:
    (workdir / ".auoreo").mkdir()
    (workdir / ".auoreo" / "HEARTBEAT.md").write_text("- check the release branch CI\n")
    out = _run(["heartbeat", "--once"]).replace("\n", "")
    assert "HEARTBEAT.md" in out and "heartbeat #1" in out
    assert "check the release branch CI" in out  # the fake gateway echoes: a non-OK report is printed
    assert "reply with exactly HEARTBEAT_OK" in out


# ------------------------------------------------------------------ config edit
def _editor(tmp_path: Path, content: str, monkeypatch: pytest.MonkeyPatch, code: int = 0) -> None:
    script = tmp_path / "ed.py"
    script.write_text(
        f"import sys, pathlib\npathlib.Path(sys.argv[1]).write_text({content!r})\nsys.exit({code})\n"
    )
    monkeypatch.setenv("EDITOR", f"{sys.executable} {script}")
    monkeypatch.delenv("VISUAL", raising=False)


def test_config_edit_saves_valid(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _editor(tmp_path, '{"port": 5050}', monkeypatch)
    assert "saved" in _run(["config", "edit"])
    assert json.loads((home / "settings.json").read_text()) == {"port": 5050}
    assert not list(home.glob("settings.edit-*"))


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("{not json", "not valid JSON"),
        ('["a"]', "must be a JSON object"),
        ('{"port": "abc"}', "invalid settings"),
        ('{"server_password": "hunter2"}', "secrets do not belong"),
    ],
)
def test_config_edit_refuses_invalid(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str, reason: str
) -> None:
    (home / "settings.json").write_text('{"port": 4100}\n')
    _editor(tmp_path, content, monkeypatch)
    out = _run(["config", "edit"], 1)
    assert reason in out and "settings.json unchanged" in out
    assert (home / "settings.json").read_text() == '{"port": 4100}\n'
    kept = list(home.glob("settings.edit-*.json"))
    assert len(kept) == 1 and kept[0].read_text() == content


def test_config_edit_editor_failure_and_no_change(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _editor(tmp_path, '{"port": 1}', monkeypatch, code=3)
    assert "editor exited with 3" in _run(["config", "edit"], 1)
    assert not (home / "settings.json").exists()
    _editor(tmp_path, "{}\n", monkeypatch)
    assert "no changes" in _run(["config", "edit"])
    monkeypatch.setenv("EDITOR", "/nonexistent/editor")
    assert "cannot start editor" in _run(["config", "edit"], 1)


# ------------------------------------------------------------------ login
OAUTH_YAML = """auth_url: https://auth.example/authorize
token_url: https://auth.example/token
client_id: demo-client
scopes: [read]
device_code_url: https://auth.example/device
"""


def test_pkce_and_authorize_url() -> None:
    verifier, challenge = oauth.pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and 43 <= len(verifier) <= 128
    cfg = oauth.OAuthConfig.model_validate(
        {"auth_url": "https://a/x?y=1", "token_url": "t", "client_id": "c"}
    )
    url = oauth.authorize_url(cfg, "http://127.0.0.1:1/callback", "st", challenge)
    assert url.startswith("https://a/x?y=1&") and "code_challenge_method=S256" in url and "state=st" in url


def test_device_flow_polls_until_granted() -> None:
    cfg = oauth.OAuthConfig.model_validate(yaml.safe_load(OAUTH_YAML))
    answers = iter(
        [
            {
                "device_code": "dc",
                "user_code": "ABCD",
                "verification_uri": "https://auth.example/d",
                "interval": 1,
            },
            {"error": "authorization_pending"},
            {"error": "slow_down"},
            {"access_token": "at", "refresh_token": "rt"},
        ]
    )
    posted: list[dict[str, str]] = []
    said: list[str] = []
    io = oauth.Io(
        say=said.append,
        ask=lambda _l: "",
        open_url=lambda _u: False,
        post=lambda url, data: (posted.append(data), next(answers))[1],
        sleep=lambda _s: None,
    )
    used, tokens = oauth.login(cfg, io)  # auto: no browser → device
    assert used == "device" and tokens["refresh_token"] == "rt"
    assert "ABCD" in said[0] and posted[-1]["grant_type"].endswith("device_code")


def test_login_manual_stores_refresh_token(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (home / "connectors" / "demo").mkdir(parents=True)
    (home / "connectors" / "demo" / "oauth.yaml").write_text(OAUTH_YAML)
    seen: dict[str, str] = {}

    def fake_post(url: str, data: dict[str, str]) -> dict[str, Any]:
        seen.update(data, url=url)
        return {"access_token": "at-secret", "refresh_token": "rt-very-secret"}

    monkeypatch.setattr(oauth, "http_post", fake_post)
    out = _run(["login", "demo", "--flow", "manual"], input="the-code\n")
    assert "refresh token stored as secret oauth/demo" in out
    assert "rt-very-secret" not in out and "at-secret" not in out
    assert (
        seen["code"] == "the-code" and seen["code_verifier"] and seen["url"] == "https://auth.example/token"
    )
    from auoreo.core.secrets import SecretStore

    assert SecretStore(home).get("oauth/demo") == "rt-very-secret"


def test_login_honest_failures(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "no connectors/nope/oauth.yaml" in _run(["login", "nope"], 1).replace("\n", "")
    (home / "connectors" / "demo").mkdir(parents=True)
    (home / "connectors" / "demo" / "oauth.yaml").write_text(OAUTH_YAML)
    monkeypatch.setattr(oauth, "http_post", lambda url, data: {"access_token": "only-access"})
    out = _run(["login", "demo", "--flow", "manual"], 1, input="c\n")
    assert "no refresh token" in out and "only-access" not in out
    monkeypatch.setattr(oauth, "http_post", lambda url, data: {"error": "invalid_grant"})
    assert "invalid_grant" in _run(["login", "demo", "--flow", "manual"], 1, input="c\n")
    assert "unknown flow" in _run(["login", "demo", "--flow", "magic"], 1)


# ------------------------------------------------------------------ new-feature / worktree gc
def _git(cwd: Path, *args: str) -> str:
    cmd = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from auoreo.runtime import worktrees as wt

    async def no_pr(_top: Path, _branch: str) -> None:
        return None

    monkeypatch.setattr(wt, "_open_pr", no_pr)
    _git(workdir, "init", "-q", "-b", "main")
    (workdir / "a.txt").write_text("a\n")
    _git(workdir, "add", ".")
    _git(workdir, "commit", "-q", "-m", "init")
    return workdir


def test_new_feature_creates_and_resumes(repo: Path) -> None:
    out = json.loads(_run(["new-feature", "auth/totp", "--no-fetch", "--json"]))
    path = Path(out["path"])
    assert path == repo / ".auoreo" / "worktrees" / "auth+totp" and out["branch"] == "auoreo/auth/totp"
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD") == "auoreo/auth/totp" and out["base"] == "HEAD"
    assert (repo / ".auoreo" / "worktrees" / ".gitignore").read_text() == "*\n"
    again = json.loads(_run(["new-feature", "auth/totp", "--json"]))
    assert again["resumed"] is True and again["path"] == out["path"]
    assert "invalid slug" in _run(["new-feature", "Bad Slug"], 1)


def test_new_feature_stops_on_open_pr(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from auoreo.runtime import worktrees as wt

    async def open_pr(_top: Path, _branch: str) -> str:
        return "https://github.com/o/r/pull/7"

    monkeypatch.setattr(wt, "_open_pr", open_pr)
    monkeypatch.setattr(wt.shutil, "which", lambda _name: "/usr/bin/gh")
    assert "pull/7" in _run(["new-feature", "x", "--no-fetch"], 1)
    _run(["new-feature", "x", "--no-fetch", "--force"])


def test_new_feature_outside_git(workdir: Path) -> None:
    assert "not inside a git repository" in " ".join(_run(["new-feature", "x"], 1).split())


def test_worktree_gc(repo: Path) -> None:
    keep = Path(json.loads(_run(["new-feature", "keep", "--no-fetch", "--json"]))["path"])
    dirty = Path(json.loads(_run(["new-feature", "dirty", "--no-fetch", "--json"]))["path"])
    (dirty / "a.txt").write_text("changed\n")
    orphan = repo / ".auoreo" / "worktrees" / "orphan"
    orphan.mkdir()
    plan = {Path(r["path"]).name: r for r in json.loads(_run(["worktree", "gc", "--dry-run", "--json"]))}
    assert plan["keep"]["action"] == "keep" and plan["dirty"]["action"] == "keep"
    assert plan["orphan"]["action"] == "remove"
    _run(["worktree", "gc", "--older-than", "0", "--yes"])
    assert not keep.exists() and not orphan.exists() and dirty.exists()
    assert "uncommitted" in _run(["worktree", "gc", "--older-than", "0", "--dry-run"])


# ------------------------------------------------------------------ eval memory
def test_eval_memory_passthrough_and_missing_script(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert "--chunk-strategy" in _run(["eval", "memory", "--", "--help"])
    monkeypatch.setenv("AUOREO_BUNDLED_ROOT", str(tmp_path))
    assert "needs a source checkout" in _run(["eval", "memory"], 1)


# ------------------------------------------------------------------ upgrade --target
def test_upgrade_target(monkeypatch: pytest.MonkeyPatch) -> None:
    from auoreo.cli.commands import upgrade as up_cli
    from auoreo.core import upgrade as up

    assert "already installed" in _run(["upgrade", "--target", __version__])
    assert "unparseable version" in _run(["upgrade", "--target", "banana"], 1)

    async def fake_run(*_a: Any, **_k: Any) -> up.Outcome:
        return up.Outcome("available", __version__, "9.9.9")

    monkeypatch.setattr(up_cli, "_run", fake_run)
    monkeypatch.setattr(up, "install_method", lambda: "uv-tool")
    out = _run(["upgrade", "--target", "9.9.8"])
    assert "offers 9.9.9, not 9.9.8" in out and "uv tool install --force auoreo==9.9.8" in out
    monkeypatch.setattr(up, "install_method", lambda: "binary")
    assert "signed binaries are only installed" in _run(["upgrade", "--target", "9.9.8"], 1).replace("\n", "")


# ------------------------------------------------------------------ debug upload
def test_debug_upload_refuses_without_endpoint(home: Path) -> None:
    assert "no upload endpoint configured" in _run(["debug", "upload", "--yes"], 1)
    (home / "settings.json").write_text(json.dumps({"cli2": {"debug_upload_url": "http://example.com/u"}}))
    assert "https (or loopback http) only" in _run(["debug", "upload", "--yes"], 1)


def test_debug_upload_sends_redacted_bundle(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import httpx

    from auoreo.cli import debug_upload

    cfg = {"cli2": {"debug_upload_url": "https://debug.example/upload", "debug_upload_secret": "debug/token"}}
    (home / "settings.json").write_text(json.dumps(cfg))
    monkeypatch.setenv("debug/token", "tok-123456789")
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, content: bytes, headers: dict[str, str], timeout: float) -> httpx.Response:
        sent.append({"url": url, "content": content, "headers": headers})
        return httpx.Response(201, json={"id": "up-1"})

    monkeypatch.setattr(debug_upload.httpx, "post", fake_post)
    assert _run(["debug", "upload"], 1, input="n\n")  # declined at the confirmation
    assert sent == []
    keep = tmp_path / "sent.zip"
    out = _run(["debug", "upload", "--yes", "--keep", str(keep)])
    assert "up-1" in out and "tok-123456789" not in out
    assert sent[0]["headers"]["Authorization"] == "Bearer tok-123456789"
    assert sent[0]["content"] == keep.read_bytes()
    with zipfile.ZipFile(keep) as zf:
        assert "manifest.json" in zf.namelist()
        assert all(b"tok-123456789" not in zf.read(n) for n in zf.namelist())


def test_redaction_gate_blocks_leaks(tmp_path: Path) -> None:
    from auoreo.cli.debug_upload import UploadRefused, redaction_gate
    from auoreo.core.settings import Settings

    archive = tmp_path / "b.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("ok.json", '{"a": 1}')
        zf.writestr("leak.txt", "token ghp_" + "a1B2" * 9)
    with pytest.raises(UploadRefused, match="leak.txt"):
        redaction_gate(Settings(), archive)


# ------------------------------------------------------------------ run --command / --share
def test_run_command_renders_slash_command() -> None:
    out = _run(["run", "--command", "goal", "ship v2", "--print"])
    assert out.startswith("echo:") and "ship v2" in out
    assert "unknown command '/nope'" in _run(["run", "--command", "nope", "x"], 2)
    assert "--interactive" in _run(["run", "--command", "goal", "-i"], 2)


def test_run_share_mints_a_working_link(home: Path) -> None:
    res = runner.invoke(app, ["run", "hello", "--share", "--format", "json"])
    assert res.exit_code == 0, res.output
    body = json.loads(res.stdout[res.stdout.index("{") : res.stdout.rindex("}") + 1])
    line = next(ln for ln in res.output.splitlines() if ln.startswith("share: "))
    token = line.split("/api/shared/", 1)[1].split()[0]
    from auoreo.automation.client import local
    from auoreo.core import sessions_more as more

    async def check(svc: Any) -> str | None:
        return await more.resolve_share(svc, token)

    assert body["status"] == "completed"
    assert local(check) == body["session_id"]  # the link resolves to the run's session
