from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from tests.cassettes import vcr_cassette_dir, vcr_config  # noqa: F401  (G5c: VCR cassettes)

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Never touch the real ~/.auoreo; never pick up keys or yolo from the host."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("AUOREO_HOME", str(home))
    monkeypatch.setenv("AUOREO_BUNDLED_ROOT", str(REPO))
    for var in (
        "AUOREO_YOLO",
        "AUOREO_GATEWAY",
        "AUOREO_MODEL",
        "AUOREO_SERVER_PASSWORD",
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GROQ_API_KEY",
        "COHERE_API_KEY",
        "AUOREO_OTEL_MODE",
        "AUOREO_PERMISSION_MODE",
        "AUOREO_SMALL_MODEL",
        "AUOREO_EMBED_MODEL",
        "AUOREO_EMBED_DIMS",
        "AUOREO_DB_ENCRYPTION",
        "AUOREO_REDIS_URL",
        "AUOREO_WORKERS",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    monkeypatch.setenv("AUOREO_OFFLINE", "1")  # doctor rtc rows: no STUN / LiveKit probes
    # The sandbox default is `auto` (bwrap on Linux when installed). The suite pins the
    # unconfined backend: tests use host-tmp fakes and persistent shells; the auto choice has
    # its own tests (tests/unit/test_security_h1a_sandbox.py).
    monkeypatch.setenv("AUOREO_BACKENDS", '{"sandbox": "none"}')
    # I4 workspace trust: the suite's project roots (`<workdir>/.auoreo`) are trusted;
    # tests/unit/test_workspace_trust.py unsets this to exercise the untrusted path.
    monkeypatch.setenv("AUOREO_SECURITY_TRUST_ALL_WORKSPACES", "1")
    # ENV: the live-settings watcher stays idle in tests (they call `poll_once()` directly) and
    # literal redactions of custom secret values never leak from one test into the next.
    from auoreo.core import live_settings, redaction

    monkeypatch.setattr(live_settings, "POLL_S", 3600.0)
    monkeypatch.setattr(redaction, "_LITERALS", {})
    yield


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    d.mkdir()
    return d


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=5)


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
async def services(settings: Settings, workdir: Path, gateway: FakeGateway) -> AsyncIterator[Services]:
    svc = await Services.create(settings, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        yield svc
    finally:
        await svc.close()


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def make_tool(root: Path, name: str, body: str = "return 'hi'", extra: str = "") -> Path:
    d = root / "tools" / name
    write(d / "tool.yaml", f"name: {name}\ndescription: test tool {name}\ndefault_action: allow\n{extra}")
    write(d / "handler.py", f"async def run(args, ctx):\n    {body}\n")
    return d
