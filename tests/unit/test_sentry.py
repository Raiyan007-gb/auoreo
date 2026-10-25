"""F3: opt-in Sentry through `core/observability.py` (fake `sentry_sdk`, no network)."""

from __future__ import annotations

import ast
import importlib.machinery
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from auoreo.core import observability as obs
from auoreo.core.settings import Settings

SRC = Path(__file__).resolve().parents[2] / "src" / "auoreo"
SECRET = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    sdk = types.ModuleType("sentry_sdk")
    sdk.init = lambda **kw: calls.append(kw)  # type: ignore[attr-defined]
    sdk.__spec__ = importlib.machinery.ModuleSpec("sentry_sdk", None)
    integrations = types.ModuleType("sentry_sdk.integrations")
    fastapi_mod = types.ModuleType("sentry_sdk.integrations.fastapi")
    starlette_mod = types.ModuleType("sentry_sdk.integrations.starlette")
    fastapi_mod.FastApiIntegration = type("FastApiIntegration", (), {})  # type: ignore[attr-defined]
    starlette_mod.StarletteIntegration = type("StarletteIntegration", (), {})  # type: ignore[attr-defined]
    for name, mod in {
        "sentry_sdk": sdk,
        "sentry_sdk.integrations": integrations,
        "sentry_sdk.integrations.fastapi": fastapi_mod,
        "sentry_sdk.integrations.starlette": starlette_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    obs._sentry.update(key=None, share="off", active=False)
    yield calls
    obs._sentry.update(key=None, share="off", active=False)


def test_unset_dsn_never_imports(fake_sdk: list[dict[str, Any]], home: Path) -> None:
    assert obs.sentry_setup(Settings(home=home)) is False
    assert fake_sdk == []
    assert obs.status(Settings(home=home))[1].startswith("off")


def test_init_args_errors_only(fake_sdk: list[dict[str, Any]], home: Path) -> None:
    st = Settings(home=home, sentry_dsn="https://k@o1.ingest.sentry.io/1")
    assert obs.sentry_setup(st) is True
    assert obs.sentry_setup(st) is True and len(fake_sdk) == 1  # idempotent
    kw = fake_sdk[0]
    assert kw["send_default_pii"] is False and kw["include_local_variables"] is False
    assert kw["traces_sample_rate"] == 0.0 and kw["max_breadcrumbs"] == 0
    assert kw["max_request_body_size"] == "never" and kw["auto_enabling_integrations"] is False
    assert [type(i).__name__ for i in kw["integrations"]] == ["StarletteIntegration", "FastApiIntegration"]
    assert kw["before_send"] is obs._before_send
    level, detail = obs.status(st)
    assert level == "ok" and "sentry: errors_only" in detail


def test_traces_only_with_full_and_otel(fake_sdk: list[dict[str, Any]], home: Path) -> None:
    dsn = "https://k@o1.ingest.sentry.io/1"
    obs.sentry_setup(Settings(home=home, sentry_dsn=dsn, sentry_share="full"))
    assert fake_sdk[-1]["traces_sample_rate"] == 0.0 and fake_sdk[-1]["max_breadcrumbs"] == 50
    obs.sentry_setup(Settings(home=home, sentry_dsn=dsn, sentry_share="full", otel_mode="local"))
    assert fake_sdk[-1]["traces_sample_rate"] == 1.0


def test_before_send_redacts_and_strips(fake_sdk: list[dict[str, Any]], home: Path) -> None:
    obs.sentry_setup(Settings(home=home, sentry_dsn="https://k@o1.ingest.sentry.io/1"))
    event = {
        "message": f"failed with key {SECRET}",
        "user": {"email": "a@b.c", "ip_address": "1.2.3.4"},
        "extra": {"prompt": "tell me secrets"},
        "breadcrumbs": {"values": [{"message": "x"}]},
        "request": {
            "method": "POST",
            "url": "http://h/api",
            "data": "PROMPT BODY",
            "headers": {"Authorization": "x"},
        },
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "stacktrace": {"frames": [{"function": "f", "vars": {"text": "PROMPT"}}]},
                }
            ]
        },
    }
    out = obs._before_send(event, {})
    assert out is not None
    assert SECRET not in str(out) and "sk-ant" in out["message"]
    assert "user" not in out and "extra" not in out and "breadcrumbs" not in out
    assert out["request"] == {"method": "POST", "url": "http://h/api"}
    assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert obs._before_breadcrumb({"message": "m", "data": {"body": "x"}}, {}) is None  # errors_only


def test_full_keeps_breadcrumbs_without_data(fake_sdk: list[dict[str, Any]], home: Path) -> None:
    obs.sentry_setup(Settings(home=home, sentry_dsn="https://k@o1.ingest.sentry.io/1", sentry_share="full"))
    crumb = obs._before_breadcrumb({"message": f"call {SECRET}", "data": {"body": "x"}}, {})
    assert crumb is not None and "data" not in crumb and SECRET not in crumb["message"]
    assert "breadcrumbs" in obs._before_send({"breadcrumbs": {"values": []}}, {})


def test_off_mode_drops_everything(fake_sdk: list[dict[str, Any]], home: Path) -> None:
    st = Settings(home=home, sentry_dsn="https://k@o1.ingest.sentry.io/1", sentry_share="off")
    assert obs.sentry_setup(st) is False and fake_sdk == []
    assert obs._before_send({"message": "x"}, {}) is None
    assert "sentry: off" in obs.status(st)[1]


def test_missing_sdk_warns(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    obs._sentry.update(key=None, share="off", active=False)
    assert obs.sentry_setup(Settings(home=home, sentry_dsn="https://k@o1.ingest.sentry.io/1")) is False
    obs._sentry.update(key=None, share="off", active=False)


def test_sentry_sdk_imported_only_by_observability() -> None:
    offenders = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(n == "sentry_sdk" or n.startswith("sentry_sdk.") for n in names):
                offenders.add(path.relative_to(SRC))
    assert offenders == {Path("core/observability.py")}
