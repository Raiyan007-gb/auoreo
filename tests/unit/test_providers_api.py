"""Settings → Models API: add (happy / invalid / error — failures store nothing), the key never in
DB rows, responses, events or audit, model cache + refresh, live model selection (the next request
uses the new model, no restart), delete guard, Ollama/Azure endpoints reaching LiteLLM params,
and the one-time `.env` import creating provider connections."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pytest_httpx import HTTPXMock
from sqlalchemy import select

from auoreo.api.app import create_app
from auoreo.core import settings_store
from auoreo.core.db import AuditRow
from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.gateway import LiteLLMGateway, credential_params  # litellm loads before httpx_mock
from tests.unit.test_loop_recovery import run, turns

KEY = "sk-proj-SECRETKEY0123456789abcdefghijklmnop"
KEY2 = "sk-proj-OTHERKEY0123456789abcdefghijklmnop"
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "providers"
OPENAI = "https://api.openai.com/v1/models"


def fx(name: str) -> Any:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


class _Reloading(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.reloads = 0

    def reload_credentials(self) -> None:
        self.reloads += 1


def _db_dump(svc: Services) -> str:
    con = sqlite3.connect(svc.settings.db_path)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return json.dumps({t: con.execute(f'SELECT * FROM "{t}"').fetchall() for t in tables}, default=str)
    finally:
        con.close()


async def _audit(svc: Services, prefix: str) -> list[dict[str, Any]]:
    async with svc.db.session() as s:
        rows = [r for r in (await s.execute(select(AuditRow))).scalars() if r.action.startswith(prefix)]
    return [{"action": r.action, **svc.audit.plain_detail(r)} for r in rows]


async def _events(svc: Services, *types: str) -> list[dict[str, Any]]:
    return [e.data for e in await svc.events.replay(0, limit=10_000) if e.type in types]


async def _connect(
    client: httpx.AsyncClient, httpx_mock: HTTPXMock, key: str = KEY, **body: Any
) -> dict[str, Any]:
    httpx_mock.add_response(url=OPENAI, json=fx("openai"))
    r = await client.post("/api/providers", json={"provider": "openai", "key": key, **body})
    assert r.status_code == 200, r.text
    return dict(r.json())


# ------------------------------------------------------------------ detect
async def test_detect_stores_nothing(services: Services, client: httpx.AsyncClient) -> None:
    r = await client.post("/api/providers/detect", json={"key": "sk-or-v1-abcdefabcdefabcdef"})
    assert r.json()["candidates"] == ["openrouter"] and r.json()["confident"]
    r = await client.post("/api/providers/detect", json={"key": "sk-0123456789abcdef0123456789"})
    assert r.json()["candidates"] == ["openai", "deepseek"] and not r.json()["confident"]
    assert [p["id"] for p in r.json()["providers"]] == ["openai", "deepseek"]
    assert services.secrets.names() == []


# ------------------------------------------------------------------ add
async def test_add_happy_path_key_only_in_the_secret_store(
    services: Services, client: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    gw = _Reloading()
    services.gateway = gw
    body = await _connect(client, httpx_mock)
    assert body["ok"] and body["model_count"] == 5 and KEY not in json.dumps(body)
    conn = body["connection"]
    assert conn["provider"] == "openai" and conn["secret_name"] == "OPENAI_API_KEY" and conn["status"] == "ok"
    assert services.secrets.get("OPENAI_API_KEY") == KEY and gw.reloads == 1
    listing = await client.get("/api/providers")
    assert KEY not in listing.text and listing.json()["connections"][0]["model_count"] == 5
    models = (await client.get(f"/api/providers/{conn['id']}/models?kind=chat")).json()
    assert [m["litellm_id"] for m in models["models"]] == ["openai/gpt-5"]
    emb = (await client.get(f"/api/providers/{conn['id']}/models?kind=embedding&q=small")).json()
    assert [m["model_id"] for m in emb["models"]] == ["text-embedding-3-small"]
    # a second key of the same provider goes to a key-pool slot, never the DB
    second = await _connect(client, httpx_mock, KEY2, label="Work")
    assert second["connection"]["secret_name"] == "provider/openai/work"
    assert services.secrets.get("provider/openai/work") == KEY2
    dump = _db_dump(services) + json.dumps(await _audit(services, "provider"))
    dump += json.dumps(await _events(services, "providers.changed", "secret.changed"))
    assert KEY not in dump and KEY2 not in dump
    assert [a["action"] for a in await _audit(services, "provider")] == ["provider.add", "provider.add"]


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": {"message": f"Incorrect API key provided: {KEY}"}}, "invalid"),
        (500, {"error": {"message": "boom"}}, "error"),
    ],
)
async def test_failed_add_returns_the_provider_error_and_stores_nothing(
    services: Services,
    client: httpx.AsyncClient,
    httpx_mock: HTTPXMock,
    status: int,
    body: dict[str, Any],
    expected: str,
) -> None:
    httpx_mock.add_response(url=OPENAI, status_code=status, json=body)
    r = await client.post("/api/providers", json={"provider": "openai", "key": KEY})
    out = r.json()
    assert r.status_code == 200 and not out["ok"] and out["status"] == expected and out["connection"] is None
    assert KEY not in r.text and out["error"]
    assert services.secrets.names() == [] and (await client.get("/api/providers")).json()["connections"] == []
    assert KEY not in _db_dump(services)


async def test_network_error_stores_nothing(
    services: Services, client: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("offline"), url=OPENAI)
    out = (await client.post("/api/providers", json={"provider": "openai", "key": KEY})).json()
    assert out["status"] == "error" and "cannot reach" in out["error"] and services.secrets.names() == []


async def test_validation_errors(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/providers", json={"provider": "nope", "key": "x"})).status_code == 404
    assert (await client.post("/api/providers", json={"provider": "openai"})).status_code == 422
    r = await client.post("/api/providers", json={"provider": "azure", "key": "k", "base_url": "https://x"})
    assert r.status_code == 422 and "deployment" in r.text
    r = await client.post("/api/providers", json={"provider": "ollama", "base_url": "file:///etc"})
    assert r.status_code == 422


# ------------------------------------------------------------------ refresh / update
async def test_refresh_failure_keeps_the_cache(
    services: Services, client: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    cid = (await _connect(client, httpx_mock))["connection"]["id"]
    httpx_mock.add_response(url=OPENAI, status_code=401, json={"error": {"message": "revoked"}})
    out = (await client.post(f"/api/providers/{cid}/refresh")).json()
    assert not out["ok"] and out["status"] == "invalid" and out["connection"]["last_error"] == "revoked"
    models = (await client.get(f"/api/providers/{cid}/models")).json()
    assert len(models["models"]) == 5 and models["status"] == "invalid"
    httpx_mock.add_response(url=OPENAI, json={"data": [{"id": "gpt-6"}]})
    out = (await client.put(f"/api/providers/{cid}", json={"key": KEY2, "label": "Main"})).json()
    assert out["ok"] and out["connection"]["label"] == "Main" and out["model_count"] == 1
    assert services.secrets.get("OPENAI_API_KEY") == KEY2


# ------------------------------------------------------------------ selection
async def test_selection_writes_db_and_the_next_request_uses_it(
    services: Services, client: httpx.AsyncClient, httpx_mock: HTTPXMock, gateway: FakeGateway
) -> None:
    cid = (await _connect(client, httpx_mock))["connection"]["id"]
    r = await client.put(
        "/api/models/selection",
        json={
            "main": {"connection_id": cid, "model_id": "gpt-5"},
            "embedding": {"connection_id": cid, "model_id": "text-embedding-3-small"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "model" in body["live"] and "embed_model" in body["restart_required"]
    assert body["selection"]["main"] | {"source": "db"} == {
        "role": "main", "value": "openai/gpt-5", "connection_id": cid, "provider": "openai",
        "model_id": "gpt-5", "source": "db",
    }  # fmt: skip
    stored = settings_store.read(services.settings.home)
    assert stored["model"] == "openai/gpt-5" and stored["embed_dims"] == 1536
    assert services.settings.model == "openai/gpt-5"  # applied in place, no restart
    gateway.queue(Scripted(text="hi"))
    status, _, _ = await run(services, "hello")
    assert status == "completed" and turns(gateway)[-1].model == "openai/gpt-5"
    assert (await _events(services, "models.changed"))[-1]["roles"] == ["embedding", "main"]
    assert (await _audit(services, "models.select"))[0]["values"]["main"] == "openai/gpt-5"
    bad = {"main": {"connection_id": cid, "model_id": "not-listed"}}
    assert (await client.put("/api/models/selection", json=bad)).status_code == 422
    emb = {"embedding": {"connection_id": cid, "model_id": "gpt-5"}}
    assert (await client.put("/api/models/selection", json=emb)).status_code == 422


async def test_selection_refused_when_the_env_wins(
    client: httpx.AsyncClient, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid = (await _connect(client, httpx_mock))["connection"]["id"]
    monkeypatch.setenv("AUOREO_MODEL", "anthropic/claude-opus-5-5")
    r = await client.put("/api/models/selection", json={"main": {"connection_id": cid, "model_id": "gpt-5"}})
    assert r.status_code == 409 and "real environment" in r.text


async def test_litellm_routes_through_a_connection_with_no_env(
    services: Services, client: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    gw = LiteLLMGateway(services.settings, services.secrets.get)
    services.gateway = gw
    cid = (await _connect(client, httpx_mock))["connection"]["id"]
    await client.put("/api/models/selection", json={"main": {"connection_id": cid, "model_id": "gpt-5"}})
    default = [e["litellm_params"] for e in gw._model_list if e["model_name"] == "default"]
    assert default == [{"api_key": KEY, "model": "openai/gpt-5", "order": 1}]


async def test_ollama_and_azure_endpoints_reach_litellm_params(
    services: Services, client: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url="http://box.lan:11434/api/tags", json=fx("ollama"))
    r = await client.post("/api/providers", json={"provider": "ollama", "base_url": "http://box.lan:11434/"})
    assert r.json()["ok"] and r.json()["model_count"] == 2
    assert (
        credential_params("ollama/llama3.2:latest", services.secrets.get)["api_base"]
        == "http://box.lan:11434"
    )
    again = await client.post("/api/providers", json={"provider": "ollama", "base_url": "http://x:1"})
    assert again.status_code == 409
    httpx_mock.add_response(
        url="https://res.openai.azure.com/openai/models?api-version=2025-04-01-preview", json={}
    )
    r = await client.post(
        "/api/providers",
        json={"provider": "azure", "key": "azkey0123456789", "base_url": "https://res.openai.azure.com",
              "api_version": "2025-04-01-preview", "deployments": ["prod-gpt5", "prod-embed-small"]},
    )  # fmt: skip
    assert r.json()["ok"] and r.json()["model_count"] == 2
    params = credential_params("azure/prod-gpt5", services.secrets.get)
    assert (
        params["api_base"] == "https://res.openai.azure.com" and params["api_version"] == "2025-04-01-preview"
    )
    assert params["api_key"] == "azkey0123456789"


# ------------------------------------------------------------------ delete
async def test_delete_guards_the_selected_model(
    services: Services, client: httpx.AsyncClient, httpx_mock: HTTPXMock
) -> None:
    cid = (await _connect(client, httpx_mock))["connection"]["id"]
    await client.put("/api/models/selection", json={"main": {"connection_id": cid, "model_id": "gpt-5"}})
    r = await client.delete(f"/api/providers/{cid}")
    assert r.status_code == 409 and "main" in r.text and services.secrets.get("OPENAI_API_KEY") == KEY
    assert (await client.delete(f"/api/providers/{cid}?force=1")).status_code == 204
    assert services.secrets.get("OPENAI_API_KEY") is None
    assert (await client.get("/api/providers")).json()["connections"] == []
    assert (await _audit(services, "provider.delete"))[0]["forced"] is True


# ------------------------------------------------------------------ .env import
async def test_import_of_a_project_env_creates_provider_connections(
    services: Services, client: httpx.AsyncClient, httpx_mock: HTTPXMock, workdir: Path
) -> None:
    (workdir / ".env").write_text(
        f"OPENAI_API_KEY={KEY}\nAUOREO_MODEL=openai/gpt-5\nAUOREO_CORS_ORIGINS=[]\nOPENAI_API_BASE=http://evil\n"
    )
    httpx_mock.add_response(url=OPENAI, json=fx("openai"))
    preview = (await client.post("/api/env/import-dotenv", json={"dry_run": True})).json()
    moves = {m["name"]: m for m in preview["moved"]}
    assert moves["OPENAI_API_KEY"]["provider"] == "openai" and moves["AUOREO_CORS_ORIGINS"]["to"] == "skipped"
    assert moves["OPENAI_API_BASE"]["to"] == "skipped" and services.secrets.names() == []
    out = (await client.post("/api/env/import-dotenv", json={})).json()
    assert out["providers"][0]["ok"] and out["files"] == [str(workdir / ".env")]
    assert "delete" in out["note"] and KEY not in json.dumps(out)
    conns = (await client.get("/api/providers")).json()["connections"]
    assert [c["provider"] for c in conns] == ["openai"]
    assert settings_store.read(services.settings.home) == {"model": "openai/gpt-5"}
    assert services.secrets.get("OPENAI_API_BASE") is None
