"""I5 CLI (2): doctor --fix + new doctor rows, named variants, `run -f` binary attachments,
`db inspect <table>`, `debug snapshot --heap` and the stream-json stdout guard."""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import struct
import zlib
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.cli.stdout_guard import StdoutGuard, active, write_line
from auoreo.core import diagnostics_i5 as d5
from auoreo.core import settings_store
from auoreo.core.settings import Settings
from auoreo.harness.discovery import default_roots
from auoreo.harness.manifests import VariantsFile
from auoreo.harness.registry import Harness
from auoreo.runtime.attachments import AttachmentError, compose, flatten, image_mime, user_message
from auoreo.runtime.run_overrides import UnknownVariant, find_variant
from tests.conftest import REPO

runner = CliRunner()


def png(w: int = 1, h: int = 1) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    )


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, workdir: Path) -> Path:
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    monkeypatch.setenv("AUOREO_PORT", "9")
    monkeypatch.chdir(workdir)
    return workdir


# ------------------------------------------------------------------ doctor --fix
@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_doctor_fix_lists_confirms_and_applies(cli_env: Path, home: Path) -> None:
    secret = home / "secrets.db"
    secret.write_bytes(b"x")
    secret.chmod(0o644)
    r = runner.invoke(app, ["doctor", "--fix"], input="n\n" * 20)
    assert r.exit_code == 0 and "skipped" in r.output and "chmod" in r.output
    assert secret.stat().st_mode & 0o777 == 0o644  # declined: untouched
    plan = json.loads(runner.invoke(app, ["doctor", "--fix", "--json"]).stdout)
    assert plan["applied"] == [] and {f["kind"] for f in plan["planned"]} >= {"chmod", "mkdir"}
    done = json.loads(runner.invoke(app, ["doctor", "--fix", "--yes", "--json"]).stdout)
    assert {f["kind"] for f in done["applied"]} >= {"chmod", "mkdir"}
    assert secret.stat().st_mode & 0o777 == 0o600 and (home / "logs").is_dir()
    assert "nothing to fix" in runner.invoke(app, ["doctor", "--fix", "--yes"]).output


# ------------------------------------------------------------------ doctor rows
def test_disk_row_thresholds(home: Path) -> None:
    st = Settings(home=home)
    assert d5.disk_row(st).level == "ok"
    st.i5.doctor.disk_warn_free_gb = 1e9
    assert d5.disk_row(st).level == "warn"
    st.i5.doctor.disk_error_free_gb = 1e9
    assert d5.disk_row(st).level == "error"


def test_settings_schema_row(home: Path) -> None:
    st = Settings(home=home)
    assert d5.settings_schema_row(st).detail.startswith("no stored settings")
    settings_store.write(home, {"teams_max_depth": "nope"})  # raw store write: no validation
    row = d5.settings_schema_row(st)
    assert row.level == "error" and "teams_max_depth" in row.detail
    settings_store.write(home, {"prot": 1})
    assert d5.settings_schema_row(st).level == "warn"
    settings_store.write(home, {"locale": "de-DE"})
    assert d5.settings_schema_row(st).level == "ok"
    con = sqlite3.connect(settings_store.db_file(home))
    con.execute("UPDATE app_settings SET value = '{oops' WHERE key = 'locale'")
    con.commit()
    con.close()
    assert d5.settings_schema_row(st).level == "error"


async def test_worktrees_tcc_and_sse_rows(home: Path, tmp_path: Path, httpx_mock: HTTPXMock) -> None:
    st = Settings(home=home)
    assert "skipped" in (await d5.worktrees_row(st, tmp_path)).detail  # not a repository
    assert d5.tcc_row("linux").detail.startswith("skipped")
    assert "skipped" in (await d5.sse_row(st, "http://127.0.0.1:9", None)).detail
    httpx_mock.add_response(
        url="http://127.0.0.1:9/metrics",
        text="# HELP active_sse_connections x\nactive_sse_connections 3\n",
    )
    row = await d5.sse_row(st, "http://127.0.0.1:9", {"status": "ok"})
    assert row.level == "ok" and row.detail.startswith("3 open")


def test_doctor_json_has_new_rows(cli_env: Path) -> None:
    rows = json.loads(runner.invoke(app, ["doctor", "--json"]).stdout)
    names = {r["check"] for r in rows}
    assert {"disk", "settings_schema", "worktrees", "desktop_tcc", "sse_connections"} <= names


# ------------------------------------------------------------------ named variants
def test_named_variants(home: Path) -> None:
    h = Harness(default_roots(home, home, REPO, []))
    deterministic = find_variant(h, "deterministic")
    assert deterministic.temperature == 0.0 and deterministic.reasoning == "off"  # type: ignore[attr-defined]
    assert find_variant(h, "kimi").interleaved_field == "reasoning_content"  # type: ignore[attr-defined]
    assert find_variant(h, "openrouter/x/y:free").reasoning == "off"  # type: ignore[attr-defined]
    from types import SimpleNamespace

    vf = VariantsFile.model_validate(
        {"variants": [{"model_regex": "^gpt"}], "named": [{"name": "deep-think"}]}
    )
    narrow = SimpleNamespace(snapshot=SimpleNamespace(variants=SimpleNamespace(manifest=vf)))
    with pytest.raises(UnknownVariant, match="deep-think"):
        find_variant(narrow, "no-such-variant")  # type: ignore[arg-type]  # names the presets
    with pytest.raises(ValueError, match="unique"):
        VariantsFile.model_validate({"variants": [{"name": "a"}], "named": [{"name": "a"}]})
    with pytest.raises(ValueError, match="needs a `name`"):
        VariantsFile.model_validate({"named": [{"temperature": 0}]})


def test_run_with_named_variant(cli_env: Path) -> None:
    r = runner.invoke(app, ["run", "hi", "--variant", "deterministic", "--format", "json"])
    assert r.exit_code == 0 and json.loads(r.stdout)["status"] == "completed", r.output


# ------------------------------------------------------------------ run -f
def test_compose_images_binaries_and_caps(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(png())
    (tmp_path / "b.bin").write_bytes(b"\x00\xff" * 10)
    (tmp_path / "c.bin").write_bytes(b"\x00" * 3000)
    from auoreo.core.settings_i5 import AttachmentSettings

    cfg = AttachmentSettings(max_inline_binary_bytes=1024)
    out = compose("look", [tmp_path / "a.png", tmp_path / "b.bin", tmp_path / "c.bin"], cfg)
    assert len(out.parts) == 1 and out.parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "[image attached]" in out.text and base64.b64encode(b"\x00\xff" * 10).decode() in out.text
    assert "over the 1024-byte inline cap" in out.text
    assert image_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp" and image_mime(b"hello") is None
    with pytest.raises(AttachmentError, match="too many images"):
        compose("x", [tmp_path / "a.png"] * 2, AttachmentSettings(max_images=1))
    with pytest.raises(AttachmentError, match="in total"):
        compose("x", [tmp_path / "c.bin"] * 2, AttachmentSettings(max_total_bytes=4000))
    msg = user_message("hi", out.parts)
    assert msg["content"][0] == {"type": "text", "text": "hi"} and flatten(msg["content"]) == "hi\n[image]"
    assert user_message("hi", ()) == {"role": "user", "content": "hi"}


def test_run_sends_image_parts(cli_env: Path) -> None:
    (cli_env / "shot.png").write_bytes(png())
    r = runner.invoke(app, ["run", "describe", "-f", "shot.png", "--format", "json"])
    assert r.exit_code == 0, r.output
    sid = json.loads(r.stdout)["session_id"]
    rows = json.loads(runner.invoke(app, ["db", "inspect", "events", "--session", sid, "--json"]).stdout)
    assert any(row["type"] == "message.created" for row in rows)
    r = runner.invoke(app, ["run", "x", "-f", "shot.png", "--attach", "http://127.0.0.1:9"])
    assert r.exit_code == 2 and "image attachments" in r.output


# ------------------------------------------------------------------ db inspect / heap
def test_db_inspect_rows(cli_env: Path) -> None:
    assert runner.invoke(app, ["run", "hello", "--format", "json"]).exit_code == 0
    events = json.loads(runner.invoke(app, ["db", "inspect", "events", "--tail", "3", "--json"]).stdout)
    assert len(events) == 3 and events[0]["seq"] < events[-1]["seq"]
    sessions = json.loads(runner.invoke(app, ["db", "inspect", "sessions", "--json"]).stdout)
    assert len(sessions) == 1 and set(sessions[0]) >= {"id", "status", "title"}
    audit = runner.invoke(app, ["db", "inspect", "audit", "--json"])
    assert audit.exit_code == 0 and isinstance(json.loads(audit.stdout), list)
    assert runner.invoke(app, ["db", "inspect", "bogus"]).exit_code == 2
    assert "schema_version" in runner.invoke(app, ["db", "inspect"]).output  # summary unchanged


def test_debug_snapshot_heap(cli_env: Path) -> None:
    r = runner.invoke(app, ["debug", "snapshot", "--heap", "--top", "5", "--json"])
    assert r.exit_code == 0, r.output
    report = json.loads(r.stdout)
    assert 1 <= len(report["top"]) <= 5 and report["peak_bytes"] >= report["current_bytes"] > 0
    assert all(str(Path.home()) not in row["where"] for row in report["top"])
    assert runner.invoke(app, ["debug", "snapshot"]).exit_code == 2


# ------------------------------------------------------------------ stdout guard
def test_stdout_guard(capsys: pytest.CaptureFixture[str]) -> None:
    with StdoutGuard():
        assert active()
        print("stray noise")
        write_line('{"ok": true}')
    assert not active()
    out = capsys.readouterr()
    assert out.out == '{"ok": true}\n' and "stray noise" in out.err


def test_stream_json_stdout_is_pure_json(cli_env: Path) -> None:
    r = runner.invoke(app, ["run", "hello", "--format", "stream-json"])
    assert r.exit_code == 0, r.output
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    assert lines and all(isinstance(json.loads(ln), dict) for ln in lines)
