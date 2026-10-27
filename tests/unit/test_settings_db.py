"""Settings in state.db: `Settings()` reads the `app_settings` table, the real environment and init
values still win, bootstrap fields stay env/CLI-only, the one-time settings.json migration (import,
rename, audit), `auoreo config` on the database, Alembic upgrade from 0007, and no `.env` loading."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from auoreo.cli.app import app
from auoreo.core import env_store, settings_store
from auoreo.core.db import SCHEMA_VERSION, AuditRow
from auoreo.core.db_migrate import migrate
from auoreo.core.services import Services
from auoreo.core.settings import Settings, write_settings_value

runner = CliRunner()


def _cli(args: list[str], code: int = 0, **kw: Any) -> str:
    res = runner.invoke(app, args, **kw)
    assert res.exit_code == code, (res.exit_code, res.output, res.exception)
    return res.output


def test_settings_reads_the_database(home: Path) -> None:
    assert Settings(home=home).locale is None and not (home / "state.db").exists()  # read never creates
    env_store.write(home, {"locale": "de-DE", "teams_max_depth": 3})
    st = Settings(home=home)
    assert st.locale == "de-DE" and st.teams_max_depth == 3
    assert Settings().locale == "de-DE"  # AUOREO_HOME (conftest) → same store


def test_env_and_init_beat_the_database(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_store.write(home, {"locale": "de-DE", "suggestions_home": "log"})
    monkeypatch.setenv("AUOREO_LOCALE", "fr-FR")
    assert Settings(home=home).locale == "fr-FR"
    assert Settings(home=home, suggestions_home="telegram").suggestions_home == "telegram"


def test_bootstrap_fields_are_never_stored(home: Path) -> None:
    for field, value in (("port", 5000), ("host", "0.0.0.0"), ("workers", 2), ("db_encryption", "columns")):
        with pytest.raises(env_store.EnvStoreError, match="not stored in the database"):
            env_store.write(home, {field: value})
    settings_store.ensure_schema(home)
    con = sqlite3.connect(settings_store.db_file(home))  # even a row written behind our back is ignored
    con.execute(
        "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES ('port', '5000', '', 'x')"
    )
    con.commit()
    con.close()
    assert Settings(home=home).port == 4096


def test_history_undo_and_version_counter(home: Path) -> None:
    v0 = settings_store.version(home)
    env_store.write(home, {"locale": "de-DE"})
    env_store.write(home, {"locale": "it-IT"})
    assert settings_store.version(home) == max(v0, 0) + 2 and env_store.history(home) == 2
    env_store.undo(home)
    assert env_store.read(home) == {"locale": "de-DE"}
    for i in range(15):
        env_store.write(home, {"locale": f"x-{i}"})
    assert env_store.history(home) == settings_store.HISTORY_KEEP


def test_write_settings_value_keeps_its_signature(home: Path) -> None:
    write_settings_value(home, "plugins.telegram.allowlist", ["42"])
    write_settings_value(home, "plugins.telegram.stream", True)
    assert env_store.read(home)["plugins"] == {"telegram": {"allowlist": ["42"], "stream": True}}


# ------------------------------------------------------------------ settings.json migration
async def test_settings_json_is_migrated_once_renamed_and_audited(home: Path, workdir: Path) -> None:
    (home / "settings.json").write_text(
        json.dumps({"locale": "pt-BR", "port": 5050, "teams_max_depth": 99, "aliases": {"fast": "groq/x"}})
    )
    st = Settings(home=home, gateway="fake")  # the first Settings() anywhere migrates
    assert st.locale == "pt-BR" and st.aliases == {"fast": "groq/x"} and st.port == 4096
    assert not (home / "settings.json").exists()
    renamed = list(home.glob("settings.json.migrated-*"))  # noqa: ASYNC240
    assert len(renamed) == 1
    assert env_store.read(home) == {"locale": "pt-BR", "aliases": {"fast": "groq/x"}}
    (home / "settings.json").write_text(json.dumps({"locale": "xx"}))  # never read again
    assert Settings(home=home).locale == "pt-BR"
    svc = await Services.create(st, cwd=workdir, with_mcp=False, with_memory=False)
    try:
        async with svc.db.session() as s:
            rows = [
                r for r in (await s.execute(select(AuditRow))).scalars() if r.action == "settings.migrate"
            ]
        detail = svc.audit.plain_detail(rows[0])
        assert detail["skipped"] == ["port"] and detail["invalid"] == ["teams_max_depth"]
        assert detail["file"] == renamed[0].name and len(rows) == 1
    finally:
        await svc.close()


def test_broken_settings_json_is_left_in_place(home: Path) -> None:
    (home / "settings.json").write_text("{ broken")
    assert Settings(home=home).locale is None
    assert (home / "settings.json").exists()


# ------------------------------------------------------------------ CLI
@pytest.fixture
def cli_home(home: Path, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    monkeypatch.setenv("AUOREO_PROFILE", "")
    monkeypatch.chdir(workdir)
    return home


def test_config_cli_on_the_database(cli_home: Path) -> None:
    assert _cli(["config", "path"]).strip() == str(cli_home / "state.db")
    _cli(["config", "set", "locale", "de-DE"])
    _cli(["config", "set", "g6.skills.taps", '["https://x/index.json"]'])
    assert json.loads(_cli(["config", "get", "locale"])) == "de-DE"
    stored = json.loads(_cli(["config", "list"]))
    assert stored == {"locale": "de-DE", "g6": {"skills": {"taps": ["https://x/index.json"]}}}
    out = _cli(["config", "set", "port", "5000"], code=1)
    assert "bootstrap" in out or "not stored" in out
    out = _cli(["config", "set", "teams_max_depth", "99"], code=1)
    assert "teams_max_depth" in out and env_store.read(cli_home)["locale"] == "de-DE"
    _cli(["config", "unset", "locale"])
    assert "locale" not in env_store.read(cli_home)
    _cli(["config", "undo"])
    assert env_store.read(cli_home)["locale"] == "de-DE"


def test_config_edit_round_trips_json(
    cli_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_store.write(cli_home, {"locale": "de-DE"})
    script = tmp_path / "ed.py"
    script.write_text(
        "import json, sys\np = sys.argv[1]\nd = json.load(open(p))\nd['locale'] = 'it-IT'\n"
        "json.dump(d, open(p, 'w'))\n"
    )
    monkeypatch.setenv("EDITOR", f"{sys.executable} {script}")
    _cli(["config", "edit"])
    assert env_store.read(cli_home) == {"locale": "it-IT"}
    script.write_text("import sys\nopen(sys.argv[1], 'w').write('{\"port\": 1}')\n")
    out = _cli(["config", "edit"], code=1)
    assert "refused" in out and env_store.read(cli_home) == {"locale": "it-IT"}


# ------------------------------------------------------------------ alembic + no .env
def test_alembic_upgrade_from_the_previous_head(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    migrate(path, "0007")
    con = sqlite3.connect(path)
    assert con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "7"
    con.close()
    migrate(path)
    con = sqlite3.connect(path)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"app_settings", "app_settings_history", "app_settings_meta"} <= tables
    assert {"provider_connections", "provider_models"} <= tables
    assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0008"
    assert con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == str(
        SCHEMA_VERSION
    )
    con.close()


def test_no_dotenv_is_ever_loaded(tmp_path: Path, home: Path) -> None:
    """A `.env` next to the CLI (project or home) changes nothing: only the web import reads it."""
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / ".env").write_text("AUOREO_LOCALE=xx-XX\nOPENAI_API_KEY=sk-from-dotenv-0123456789\n")
    (home / ".env").write_text("AUOREO_LOCALE=yy-YY\n")
    code = (
        "import os, sys; sys.argv = ['auoreo', 'config', 'get', 'locale']\n"
        "from auoreo.cli.main import main\n"
        "main()\n"
        "assert 'OPENAI_API_KEY' not in os.environ\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=proj,
        capture_output=True,
        text=True,
        check=False,
        env={
            "AUOREO_HOME": str(home),
            "PATH": "/usr/bin:/bin",
            "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
        },
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "null"
    src = (Path(__file__).resolve().parents[2] / "src" / "auoreo" / "cli" / "main.py").read_text()
    assert "load_env_files" not in src and "core.dotenv" not in src
