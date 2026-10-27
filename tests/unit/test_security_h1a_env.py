"""H1a: no `.env` is ever loaded and the one-time import never moves security knobs; yolo is
frozen from the real env (§19.3); secrets come from env → keyring → secrets.db with a
cross-process lock on writes (§7); the closed child env allowlist (§24.9); forced redaction for
debug bundles (§24.9)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from auoreo.core import redaction
from auoreo.core.debuginfo import build_bundle
from auoreo.core.env_import import plan
from auoreo.core.secrets import ENV_ALLOWLIST, SecretStore, child_env
from auoreo.core.settings import Settings

REPO = Path(__file__).resolve().parents[2]
FAKE_KEY = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0" * 2


# ------------------------------------------------------------------ §19.3
def test_dotenv_import_refuses_security_knobs(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "AUOREO_YOLO=1\nAUOREO_PERMISSION_MODE=yolo\nAUOREO_SANDBOX_POLICY=warn\n"
        'AUOREO_REDACT_SECRETS=0\nAUOREO_BACKENDS={"sandbox":"none"}\nAUOREO_SECURITY={}\n'
        "H1A_PROVIDER_KEY=abc123\n"
    )
    moves, settings, secrets = plan([tmp_path / ".env"], tmp_path)
    assert secrets == {"H1A_PROVIDER_KEY": "abc123"} and settings == {}
    assert {m.name for m in moves if m.to == "skipped"} == {
        "AUOREO_YOLO", "AUOREO_PERMISSION_MODE", "AUOREO_SANDBOX_POLICY", "AUOREO_REDACT_SECRETS",
        "AUOREO_BACKENDS", "AUOREO_SECURITY",
    }  # fmt: skip


def _probe(cwd: Path, env: dict[str, str]) -> dict[str, object]:
    code = (
        "import json, os\n"
        "from auoreo.cli import main as m\n"
        "import auoreo.core.permissions, auoreo.core.redaction\n"
        "from auoreo.core import permissions as p, redaction as r\n"
        "print(json.dumps({'yolo': p.YOLO_FROZEN, 'redact': r.ENABLED,"
        " 'env': os.environ.get('AUOREO_YOLO'), 'other': os.environ.get('H1A_OTHER')}))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True, timeout=60
    )
    assert out.returncode == 0, out.stderr
    return dict(json.loads(out.stdout.strip().splitlines()[-1]))


def test_project_env_cannot_turn_on_yolo_or_disable_redaction(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("AUOREO_YOLO=1\nAUOREO_REDACT_SECRETS=0\nH1A_OTHER=ok\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AUOREO_YOLO", "AUOREO_REDACT"))}
    env["AUOREO_HOME"] = str(tmp_path / "home")
    got = _probe(tmp_path, env)
    assert got == {"yolo": False, "redact": True, "env": None, "other": None}  # never loaded
    # the real process environment still works
    got = _probe(tmp_path, {**env, "AUOREO_YOLO": "1"})
    assert got["yolo"] is True


def test_cli_main_freezes_and_never_loads_dotenv() -> None:
    src = (REPO / "src/auoreo/cli/main.py").read_text()
    assert "import auoreo.core.permissions" in src and "load_env_files" not in src


# ------------------------------------------------------------------ §7 precedence
def test_a_dotenv_file_is_never_a_secret_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = SecretStore(tmp_path / "home")
    store.set("H1A_TOKEN", "from-store")
    (tmp_path / ".env").write_text("H1A_TOKEN=from-dotenv\nH1A_ONLY_DOTENV=gap-filler\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("H1A_TOKEN", raising=False)
    monkeypatch.delenv("H1A_ONLY_DOTENV", raising=False)
    assert store.get("H1A_TOKEN") == "from-store" and store.get("H1A_ONLY_DOTENV") is None
    assert child_env(["H1A_TOKEN"], store)["H1A_TOKEN"] == "from-store"
    monkeypatch.setenv("H1A_TOKEN", "from-real-env")  # the process env always wins
    assert store.get("H1A_TOKEN") == "from-real-env"


def test_secret_writes_are_serialised_across_processes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    SecretStore(home).set("seed", "0")  # creates the key file first
    code = (
        "import sys\nfrom pathlib import Path\nfrom auoreo.core.secrets import SecretStore\n"
        "s = SecretStore(Path(sys.argv[1]))\n"
        "for i in range(15): s.set(f'{sys.argv[2]}-{i}', str(i))\n"
    )
    env = {**os.environ, "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring"}
    procs = [subprocess.Popen([sys.executable, "-c", code, str(home), f"w{n}"], env=env) for n in range(4)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    names = SecretStore(home).names()
    assert len(names) == 1 + 4 * 15  # no lost update
    assert (home / "secrets.db.lock").exists()


# ------------------------------------------------------------------ §24.9 child env
def test_child_env_allowlist_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert set(ENV_ALLOWLIST) == {"PATH", "HOME", "LANG", "TZ"}
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("USER", "someone")
    monkeypatch.setenv("AUOREO_TELEMETRY_DISABLED", "1")
    monkeypatch.setenv("H1A_SECRET_TOKEN", "x" * 20)
    env = child_env([], None)
    assert "TERM" not in env and "USER" not in env and "H1A_SECRET_TOKEN" not in env
    assert env["AUOREO_TELEMETRY_DISABLED"] == "1"


# ------------------------------------------------------------------ §24.9 forced redaction
def test_redact_force_ignores_the_global_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redaction, "ENABLED", False)
    text = f"key {FAKE_KEY}"
    assert redaction.redact(text) == text
    assert FAKE_KEY not in redaction.redact(text, force=True)
    assert redaction.pattern_count() > len(redaction.ALL_PREFIXES)


async def test_debug_bundle_redacts_even_when_redaction_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(redaction, "ENABLED", False)
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    (home / "server.log").write_text(f"boot ok\nusing {FAKE_KEY}\n")
    st = Settings(home=home, gateway="fake")
    out = tmp_path / "bundle.zip"
    members = await build_bundle(st, out)
    assert "manifest.json" in members and "logs/server.log" in members
    with zipfile.ZipFile(out) as zf:
        blob = "".join(zf.read(n).decode() for n in zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
    assert FAKE_KEY not in blob
    assert manifest["redaction"]["pattern_count"] > 0 and manifest["redaction"]["version"]


def test_project_dotenv_cannot_repoint_home(tmp_path: Path) -> None:
    """A repo `.env` must not swap in its own state.db via AUOREO_HOME / AUOREO_PROFILE."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("AUOREO_HOME=./evil\nAUOREO_PROFILE=evil\nSOME_OK_TOKEN=1\n")
    moves, settings, secrets = plan([proj / ".env"], tmp_path / "home")
    to = {m.name: m.to for m in moves}
    assert to["AUOREO_HOME"] == "skipped" and to["AUOREO_PROFILE"] == "skipped"
    assert settings == {} and secrets == {"SOME_OK_TOKEN": "1"}
