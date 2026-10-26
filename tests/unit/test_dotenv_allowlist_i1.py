"""I1: a project `.env` is allowlisted — it can set provider keys and model names, never
CORS origins, model api_base overrides, the bind host or the server password (§7, §19.3)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from auoreo.core import dotenv

BLOCKED = (
    "AUOREO_CORS_ORIGINS",
    "AUOREO_MODELS",
    "AUOREO_HOST",
    "AUOREO_SERVER_PASSWORD",
    "AUOREO_SEARXNG_URL",
    "OPENAI_API_BASE",
    "OPENAI_BASE_URL",
    "HTTPS_PROXY",
    "LD_PRELOAD",
    "PYTHONPATH",
)
ALLOWED = ("I1_PROVIDER_API_KEY", "I1_GH_TOKEN", "AUOREO_MODEL", "AUOREO_GATEWAY")


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(dotenv, "_ORIGIN", {})
    monkeypatch.setenv("AUOREO_HOME", str(tmp_path / "home"))
    for name in (*BLOCKED, *ALLOWED):
        monkeypatch.delenv(name, raising=False)


def test_project_dotenv_ignores_everything_outside_the_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    values = {
        "AUOREO_CORS_ORIGINS": '["https://evil.example"]',
        "AUOREO_MODELS": '{"default": {"models": ["openai/x"], "api_base": "https://evil.example"}}',
        "AUOREO_HOST": "0.0.0.0",
        "AUOREO_SERVER_PASSWORD": "attacker",
        "AUOREO_SEARXNG_URL": "https://evil.example",
        "OPENAI_API_BASE": "https://evil.example",
        "OPENAI_BASE_URL": "https://evil.example",
        "HTTPS_PROXY": "http://evil.example:8080",
        "LD_PRELOAD": "/tmp/evil.so",
        "PYTHONPATH": "/tmp/evil",
        "I1_PROVIDER_API_KEY": "k",
        "I1_GH_TOKEN": "t",
        "AUOREO_MODEL": "openai/gpt-5",
        "AUOREO_GATEWAY": "fake",
    }
    (tmp_path / ".env").write_text("".join(f"{k}={v!r}\n" for k, v in values.items()))
    for name in values:
        monkeypatch.delenv(name, raising=False)
    caplog.set_level(logging.WARNING, logger="auoreo.core.dotenv")
    loaded = dotenv.load_env_files(tmp_path)
    try:
        assert sorted(loaded) == sorted(ALLOWED)
        for name in BLOCKED:
            assert name not in os.environ, name
        assert "AUOREO_CORS_ORIGINS" in caplog.text and "AUOREO_SERVER_PASSWORD" in caplog.text
    finally:
        for name in loaded:
            os.environ.pop(name, None)


def test_home_dotenv_may_still_set_server_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / ".env").write_text("AUOREO_HOST=0.0.0.0\nAUOREO_SERVER_PASSWORD=mine\nAUOREO_YOLO=1\n")
    for name in ("AUOREO_HOST", "AUOREO_SERVER_PASSWORD", "AUOREO_YOLO"):
        monkeypatch.delenv(name, raising=False)
    loaded = dotenv.load_env_files(tmp_path / "proj")
    try:
        assert sorted(loaded) == ["AUOREO_HOST", "AUOREO_SERVER_PASSWORD"]  # security knobs still never
    finally:
        for name in loaded:
            os.environ.pop(name, None)


@pytest.mark.parametrize("name", BLOCKED)
def test_project_allowed_predicate(name: str) -> None:
    assert not dotenv.project_allowed(name)


@pytest.mark.parametrize("name", [*ALLOWED, "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY"])
def test_project_allowed_predicate_accepts_credentials(name: str) -> None:
    assert dotenv.project_allowed(name)
