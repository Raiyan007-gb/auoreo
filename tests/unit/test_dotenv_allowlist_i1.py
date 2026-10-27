"""I1: a project `.env` is repository-controlled. AUOREO never loads it; the one-time web import
holds it to an allowlist — provider keys and model names move into the stores, never CORS origins,
model api_base overrides, the bind host or the server password (§7, §19.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.core import env_rules
from auoreo.core.env_import import plan

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


def test_project_dotenv_import_skips_everything_outside_the_allowlist(tmp_path: Path) -> None:
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
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("".join(f"{k}={v!r}\n" for k, v in values.items()))
    moves, settings, secrets = plan([proj / ".env"], tmp_path / "home")
    to = {m.name: m.to for m in moves}
    assert {n for n, t in to.items() if t in {"settings", "secrets"}} == set(ALLOWED)
    assert all(to[n] in {"skipped", "kept"} for n in BLOCKED)
    assert settings == {"model": "openai/gpt-5", "gateway": "fake"}
    assert secrets == {"I1_PROVIDER_API_KEY": "k", "I1_GH_TOKEN": "t"}


def test_home_dotenv_import_keeps_the_blocklist_only(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / ".env").write_text("AUOREO_SEARXNG_URL=http://s:8888\nAUOREO_HOST=0.0.0.0\nAUOREO_YOLO=1\n")
    moves, settings, _ = plan([home / ".env"], home)
    to = {m.name: m.to for m in moves}
    assert settings == {"searxng_url": "http://s:8888"}
    assert to["AUOREO_YOLO"] == "skipped" and to["AUOREO_HOST"] == "skipped"  # env-only / bootstrap


@pytest.mark.parametrize("name", BLOCKED)
def test_project_allowed_predicate(name: str) -> None:
    assert not env_rules.project_allowed(name)


@pytest.mark.parametrize("name", [*ALLOWED, "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY"])
def test_project_allowed_predicate_accepts_credentials(name: str) -> None:
    assert env_rules.project_allowed(name)
