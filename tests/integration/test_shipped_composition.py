"""G5c: REAL-composition boot test (spec §20.4).

Unlike the other integration tests (which inject a `services` fixture with an in-memory
database and no extensions), this boots exactly what `auoreo serve` builds: `create_app()`
owns the lifespan, `Services.create()` discovers the *bundled* definitions, migrates a file
`state.db` with Alembic, opens memory, starts the extension starters, and builds the
gateway from settings (`gateway=fake`, so no keys). One session then runs end-to-end
through the HTTP API with auth on.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core import metrics_text
from auoreo.core.services import Services
from auoreo.core.settings import Settings

REPO = Path(__file__).resolve().parents[2]
PW = "shipped-composition-pw"
AUTH = {"authorization": f"Bearer {PW}"}


@pytest.fixture
async def booted(home: Path, workdir: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    metrics_text.reset()
    settings = Settings(home=home, gateway="fake", server_password=PW, approval_timeout_s=5)
    app = create_app(settings, cwd=workdir)  # no services= → the app owns the real composition
    async with (
        LifespanManager(app, startup_timeout=60, shutdown_timeout=60),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c, app.state.services
    metrics_text.reset()


def _sse(text: str) -> list[dict[str, object]]:
    out = []
    for block in text.split("\n\n"):
        data = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
        if data:
            out.append(json.loads("\n".join(data)))
    return out


def _bundled(kind: str) -> set[str]:
    return {p.name for p in (REPO / kind).iterdir() if p.is_dir() and not p.name.startswith((".", "_"))}


async def test_real_composition_boots_and_runs_one_session(
    booted: tuple[httpx.AsyncClient, Services], home: Path
) -> None:
    client, svc = booted

    # composition: file database migrated in the temp home, fake gateway from settings
    assert svc.settings.home == home and (home / "state.db").is_file()
    assert svc.memory is not None and svc.gateway.__class__.__name__ == "FakeGateway"
    health = (await client.get("/api/health")).json()
    assert health["status"] == "ok" and health["gateway"] == "fake"

    # auth is on for every API route
    assert (await client.get("/api/harness")).status_code == 401
    assert (await client.get("/metrics")).status_code == 401

    # catalog: every bundled agent and tool folder is discovered without errors
    listing = (await client.get("/api/harness", headers=AUTH)).json()
    assert listing["errors"] == []
    names = {kind: {e["name"] for e in rows} for kind, rows in listing["entries"].items()}
    assert _bundled("agents") <= names["agents"]
    assert _bundled("tools") <= names["tools"]
    assert svc.settings.agent in names["agents"] and svc.settings.workflow in names["workflows"]

    # one session end-to-end through the API
    r = await client.post("/api/chat", json={"message": "hello"}, headers=AUTH)
    assert r.status_code == 202
    sid = r.json()["session_id"]
    await svc.coordinator.wait(sid, 30)
    await svc.coordinator.background_idle(30)
    r = await client.get("/api/events", params={"session_id": sid, "follow": "false"}, headers=AUTH)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert r.headers["cache-control"] == "no-cache"
    events = [e for e in _sse(r.text) if e["type"] != "session.title_updated"]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "session.created" and kinds[-1] == "run.completed"
    assert events[-1]["data"]["text"] == "echo: hello"  # type: ignore[index]

    detail = (await client.get(f"/api/sessions/{sid}", headers=AUTH)).json()
    assert detail["id"] == sid and detail["agent"] == svc.settings.agent
    messages = (await client.get(f"/api/sessions/{sid}/messages", headers=AUTH)).json()
    assert [m["role"] for m in messages][:2] == ["user", "assistant"]
    usage = (await client.get("/api/usage", params={"session_id": sid}, headers=AUTH)).json()
    assert usage and all(u["session_id"] == sid for u in usage)

    # the /metrics fallback saw the run (OTel is off in tests)
    text = (await client.get("/metrics", headers=AUTH)).text
    assert 'run_completed_total{status="completed"} 1' in text
