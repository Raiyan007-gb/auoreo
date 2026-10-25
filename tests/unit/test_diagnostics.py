"""`auoreo.core.diagnostics`: the shared doctor checks and the EXTRA_CHECKS extension point."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.core import diagnostics
from auoreo.core.diagnostics import Check
from auoreo.core.settings import Settings


async def test_run_checks_without_services(home: Path) -> None:
    rows = await diagnostics.run_checks(Settings(home=home, gateway="fake"))
    names = [c.check for c in rows]
    assert names[:4] == ["python", "sqlite", "sqlite-vec", "home"]
    assert "harness" in names and names[-2:] == ["yolo", "otel"]
    assert "audit" not in names  # no state.db yet
    assert all(isinstance(c, Check) and c.level in {"ok", "warn", "error"} for c in rows)
    cred = next(c for c in rows if c.check == "credentials")
    assert cred.level == "ok" and "fake gateway" in cred.detail


async def test_extra_checks_append_and_contain_failures(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def otel(st: Settings) -> list[Check]:
        return [Check("otel", "warn", "exporter not configured", "set OTEL_EXPORTER_OTLP_ENDPOINT")]

    async def broken(st: Settings) -> list[Check]:
        raise RuntimeError("boom")

    monkeypatch.setattr(diagnostics, "EXTRA_CHECKS", [otel, broken])
    rows = await diagnostics.run_checks(Settings(home=home, gateway="fake"))
    assert rows[-2] == Check("otel", "warn", "exporter not configured", "set OTEL_EXPORTER_OTLP_ENDPOINT")
    assert rows[-1].check == "extra:broken" and "boom" in rows[-1].detail


def test_credential_checks_never_echo_values(home: Path) -> None:
    secret = "sk-ant-thisisaverysecretvalue123456"
    st = Settings(home=home, gateway="litellm", model="anthropic/claude-sonnet-4-5")
    rows = diagnostics.credential_checks(st, {"ANTHROPIC_API_KEY": secret}.get)
    assert rows and all(secret not in c.detail and secret not in c.remedy for c in rows)
    assert rows[0].level == "ok" and "ANTHROPIC_API_KEY" in rows[0].detail
    missing = diagnostics.credential_checks(st, lambda _k: None)
    assert missing[0].level == "warn" and "auoreo secrets set" in missing[0].remedy


def test_summarize_and_simple_checks(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [Check("a", "ok", ""), Check("b", "warn", ""), Check("c", "error", ""), Check("d", "ok", "")]
    assert diagnostics.summarize(rows) == {"ok": 2, "warn": 1, "error": 1}
    monkeypatch.setenv("AUOREO_YOLO", "1")
    assert diagnostics.yolo_checks()[0].level == "warn"
    st = Settings(home=home, host="0.0.0.0", server_password=None)
    assert diagnostics.auth_checks(st)[0].level == "error"
    assert diagnostics.port_checks(st, serving=True)[0].detail.endswith("serving (this process)")
    assert Check("x", "ok", "d").as_dict() == {"check": "x", "level": "ok", "detail": "d", "remedy": ""}


def test_cli_doctor_json_is_unchanged_shape(home: Path) -> None:
    from auoreo.cli.app import app

    res = CliRunner().invoke(app, ["doctor", "--json"])
    rows = json.loads(res.stdout)
    assert all(set(r) == {"check", "level", "detail", "remedy"} for r in rows)
    assert [r["check"] for r in rows][:4] == ["python", "sqlite", "sqlite-vec", "home"]
