"""ENV: `/api/env` — catalog ↔ Settings parity, live-applied settings writes, write-only secrets,
env/env-only locks, undo, `.env` import and the capability-token refusal."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from auoreo.api.app import create_app
from auoreo.core.db import AuditRow
from auoreo.core.env_catalog import by_field, by_name, catalog, entries, secret_names
from auoreo.core.services import Services
from auoreo.core.session_jwt import tokens_for
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway

SECRET = "sk-test-abcdefghijklmnopqrstuvwxyz0123456789"


class _Reloading(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.reloads = 0

    def reload_credentials(self) -> None:
        self.reloads += 1


@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


def _entry(body: dict[str, Any], name: str) -> dict[str, Any]:
    for sec in body["sections"]:
        for e in sec["entries"]:
            if e["name"] == name:
                return e
    raise AssertionError(name)


async def _events(svc: Services, type_: str) -> list[dict[str, Any]]:
    rows = await svc.events.replay(0, limit=10_000)
    return [e.data for e in rows if e.type == type_]


async def _audit(svc: Services, action: str) -> list[dict[str, Any]]:
    async with svc.db.session() as s:
        rows = [r for r in (await s.execute(select(AuditRow))).scalars() if r.action == action]
    return [svc.audit.plain_detail(r) for r in rows]


# ------------------------------------------------------------------ catalog
def test_catalog_covers_every_settings_field_once() -> None:
    fields = [e.field for e in entries() if e.field]
    assert sorted(fields) == sorted(Settings.model_fields)
    assert len(fields) == len(set(fields))
    sections, _ = catalog()
    assert [s.id for s in sections][:3] == ["models", "providers", "tool_keys"]
    assert by_field()["permission_mode"].type == "enum"
    assert by_field()["permission_mode"].choices == ("manual", "smart", "yolo", "plan", "acceptEdits")
    assert by_field()["permission_mode"].tier == "env-only" and not by_field()["permission_mode"].live
    assert by_field()["port"].restart_reason and by_field()["locale"].live
    assert by_field()["models"].type == "json" and by_field()["strict_overlays"].type == "bool"
    assert "OPENAI_API_KEY" in secret_names() and "AUOREO_YOLO" not in secret_names()
    assert by_name()["GH_HOST"].kind == "passthrough"


# ------------------------------------------------------------------ listing
async def test_listing_reports_sources_and_never_secret_values(
    services: Services, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    services.secrets.set("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-from-the-real-environment-123456")
    r = await client.get("/api/env")
    assert r.status_code == 200, r.text
    assert SECRET not in r.text and "gsk-from-the-real" not in r.text
    body = r.json()
    openai = _entry(body, "OPENAI_API_KEY")
    assert openai["is_set"] and openai["source"] == "secrets" and openai["value"] is None
    assert openai["updated_at"] is not None and openai["writable"]
    groq = _entry(body, "GROQ_API_KEY")
    assert groq["source"] == "env" and not groq["writable"] and "real environment" in groq["locked_reason"]
    backends = _entry(body, "AUOREO_BACKENDS")  # conftest exports it
    assert backends["source"] == "env" and backends["tier"] == "env-only" and not backends["writable"]
    assert backends["value"] == {"sandbox": "none"}
    gateway = _entry(body, "AUOREO_GATEWAY")  # Settings(gateway="fake") in the fixture
    assert gateway["source"] == "override" and gateway["value"] == "fake"
    locale = _entry(body, "AUOREO_LOCALE")
    assert locale["source"] == "default" and locale["writable"] and locale["live"]


# ------------------------------------------------------------------ settings writes
async def test_put_setting_writes_file_and_applies_live(
    services: Services, client: httpx.AsyncClient
) -> None:
    gw = _Reloading()
    services.gateway = gw
    same = services.settings
    r = await client.put("/api/env/settings/AUOREO_LOCALE", json={"value": "de-DE"})
    assert r.status_code == 200, r.text
    assert r.json()["live"] == ["locale"] and "next step" in r.json()["note"]
    assert services.settings is same and same.locale == "de-DE"  # in place
    assert json.loads((services.settings.home / "settings.json").read_text())["locale"] == "de-DE"
    if os.name == "posix":
        assert (services.settings.home / "settings.json").stat().st_mode & 0o777 == 0o600
    r = await client.put("/api/env/settings/AUOREO_MODEL", json={"value": "openai/gpt-5"})
    assert r.status_code == 200 and "model" in r.json()["live"]
    assert services.settings.models["default"].models == ["openai/gpt-5"] and gw.reloads == 1
    changed = await _events(services, "settings.changed")
    assert changed[-1]["keys"] == ["model", "models"] and changed[-1]["source"] == "api"
    assert await _audit(services, "settings.write") == [{"keys": ["locale"]}, {"keys": ["model"]}]
    r = await client.put("/api/env/settings/AUOREO_PORT", json={"value": 5000})
    assert r.json()["restart_required"] == ["port"] and services.settings.port == 4096
    assert _entry((await client.get("/api/env")).json(), "AUOREO_PORT")["pending_restart"]
    r = await client.put("/api/env/settings/AUOREO_LOCALE", json={"value": None})  # reset
    assert r.status_code == 200 and services.settings.locale is None


async def test_invalid_value_is_422_and_file_untouched(services: Services, client: httpx.AsyncClient) -> None:
    await client.put("/api/env/settings/AUOREO_LOCALE", json={"value": "fr-FR"})
    path = services.settings.home / "settings.json"
    before = path.read_bytes()
    r = await client.put("/api/env/settings/AUOREO_TEAMS_MAX_DEPTH", json={"value": 99})
    assert r.status_code == 422 and "teams_max_depth" in r.text
    r = await client.put("/api/env/settings/AUOREO_UPDATE_CHANNEL", json={"value": "nightly"})
    assert r.status_code == 422
    assert path.read_bytes() == before and services.settings.teams_max_depth == 2


async def test_env_sourced_409_env_only_403_unknown_404(
    services: Services, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUOREO_SUGGESTIONS_HOME", "telegram")
    r = await client.put("/api/env/settings/AUOREO_SUGGESTIONS_HOME", json={"value": "log"})
    assert r.status_code == 409 and "real environment" in r.text
    r = await client.put("/api/env/settings/AUOREO_PERMISSION_MODE", json={"value": "yolo"})
    assert r.status_code == 403 and services.settings.permission_mode == "manual"
    assert (await client.put("/api/env/settings/AUOREO_ENV", json={"value": {}})).status_code == 403
    assert (await client.put("/api/env/settings/NOPE", json={"value": 1})).status_code == 404
    assert (
        await client.put("/api/env/secrets/AUOREO_SERVER_PASSWORD", json={"value": "x"})
    ).status_code == 403
    assert (await client.put("/api/env/secrets/GH_HOST", json={"value": "x"})).status_code == 404
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-real-env-value-0123456789")
    assert (
        await client.put("/api/env/secrets/ANTHROPIC_API_KEY", json={"value": "x" * 10})
    ).status_code == 409
    assert not (services.settings.home / "settings.json").exists()


# ------------------------------------------------------------------ secrets
async def test_secret_set_rotate_delete_never_echo(services: Services, client: httpx.AsyncClient) -> None:
    gw = _Reloading()
    services.gateway = gw
    r = await client.put("/api/env/secrets/EXA_API_KEY", json={"value": SECRET})
    assert r.status_code == 200 and SECRET not in r.text
    assert services.secrets.get("EXA_API_KEY") == SECRET and gw.reloads == 1
    r = await client.post("/api/env/secrets/EXA_API_KEY/rotate", json={"value": SECRET + "2", "grace_s": 60})
    assert r.status_code == 200 and SECRET not in r.text
    assert services.secrets.candidates("EXA_API_KEY") == [SECRET + "2", SECRET]
    listing = await client.get("/api/env")
    assert SECRET not in listing.text and _entry(listing.json(), "EXA_API_KEY")["is_set"]
    r = await client.delete("/api/env/secrets/EXA_API_KEY")
    assert r.status_code == 200 and services.secrets.get("EXA_API_KEY") is None
    events = await _events(services, "secret.changed")
    assert [e["action"] for e in events] == ["set", "rotate", "delete"]
    audits = await _audit(services, "secret.write")
    assert [a["name"] for a in audits] == ["EXA_API_KEY"] * 3
    assert SECRET not in json.dumps(events) + json.dumps(audits)
    assert "EXA_API_KEY#updated_at" not in services.secrets.names()


# ------------------------------------------------------------------ undo + import
async def test_undo_restores_previous_settings(services: Services, client: httpx.AsyncClient) -> None:
    await client.put("/api/env/settings/AUOREO_LOCALE", json={"value": "de-DE"})
    await client.put("/api/env/settings/AUOREO_LOCALE", json={"value": "it-IT"})
    assert services.settings.locale == "it-IT"
    r = await client.post("/api/env/undo")
    assert r.status_code == 200 and r.json()["live"] == ["locale"]
    assert services.settings.locale == "de-DE"
    assert json.loads((services.settings.home / "settings.json").read_text())["locale"] == "de-DE"
    assert (await client.post("/api/env/undo")).status_code == 200  # back to "no file" ({})
    assert services.settings.locale is None
    assert (await client.post("/api/env/undo")).status_code == 409
    assert await _audit(services, "settings.undo")


async def test_import_dotenv_moves_keys_and_skips_protected(
    services: Services, client: httpx.AsyncClient
) -> None:
    home: Path = services.settings.home
    (home / ".env").write_text(
        f"OPENAI_API_KEY={SECRET}\nAUOREO_LOCALE=pt-BR\nAUOREO_YOLO=1\n"
        'AUOREO_PERMISSION_MODE=yolo\nSOME_OTHER=thing\nAUOREO_CORS_ORIGINS=["https://x.example"]\n'
    )
    preview = (await client.post("/api/env/import-dotenv", json={"dry_run": True})).json()
    assert preview["dry_run"] and not (home / "settings.json").exists()
    r = await client.post("/api/env/import-dotenv", json={})
    assert r.status_code == 200 and SECRET not in r.text
    moved = {m["name"]: m["to"] for m in r.json()["moved"]}
    assert moved == {
        "OPENAI_API_KEY": "secrets",
        "AUOREO_LOCALE": "settings",
        "AUOREO_YOLO": "skipped",
        "AUOREO_PERMISSION_MODE": "skipped",
        "SOME_OTHER": "kept",
        "AUOREO_CORS_ORIGINS": "settings",
    }
    data = json.loads((home / "settings.json").read_text())
    assert data == {"locale": "pt-BR", "cors_origins": ["https://x.example"]}
    assert "permission_mode" not in data and services.settings.permission_mode == "manual"
    assert services.secrets.get("OPENAI_API_KEY") == SECRET and services.settings.locale == "pt-BR"
    assert (await _audit(services, "env.import"))[0]["counts"]["secrets"] == 1


# ------------------------------------------------------------------ auth
async def test_capability_token_is_refused(services: Services, client: httpx.AsyncClient) -> None:
    services.settings.server_password = "pw-123456"
    token, _ = tokens_for(services).mint(ip=None, kind="cap", session_id="ses_x")
    r = await client.get("/api/env", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code in (401, 403)
    r = await client.get("/api/env", headers={"Authorization": "Bearer pw-123456"})
    assert r.status_code == 200
    assert (await client.get("/api/env")).status_code == 401
    big = {"value": "x" * 70_000}
    r = await client.put(
        "/api/env/settings/AUOREO_LOCALE", json=big, headers={"Authorization": "Bearer pw-123456"}
    )
    assert r.status_code == 413
