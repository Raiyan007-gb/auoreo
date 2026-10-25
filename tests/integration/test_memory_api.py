"""M5f memory lifecycle + curator endpoints."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


async def test_fact_forget_pin_feedback(client: httpx.AsyncClient, services: Services) -> None:
    fid = (
        await client.post("/api/memory/facts", json={"content": "The VPN endpoint is vpn.example.org"})
    ).json()["id"]
    assert services.memory is not None
    assert (await services.memory.get_fact(fid) or {})["source"] == "user"
    r = await client.post(f"/api/memory/facts/{fid}/pin", json={"pinned": True})
    assert r.status_code == 200 and r.json() == {"id": fid, "ok": True, "trust": None}
    r = await client.post(f"/api/memory/facts/{fid}/feedback", json={"helpful": True})
    assert r.status_code == 200 and r.json()["trust"] == pytest.approx(0.55)
    r = await client.post(f"/api/memory/facts/{fid}/forget")
    assert r.status_code == 200 and r.json()["ok"] is True
    assert (await client.post(f"/api/memory/facts/{fid}/forget")).json()["ok"] is False  # already archived
    hits = (await client.get("/api/memory/search", params={"q": "vpn endpoint"})).json()
    assert all(h["id"] != fid for h in hits if h["kind"] == "fact")
    for path, body in (("forget", None), ("pin", {"pinned": False}), ("feedback", {"helpful": False})):
        r = await client.post(f"/api/memory/facts/99999/{path}", json=body)
        assert r.status_code == 404, path
    assert (await client.post(f"/api/memory/facts/{fid}/feedback", json={})).status_code == 422


async def test_curator_run_and_report(client: httpx.AsyncClient, services: Services) -> None:
    assert (await client.get("/api/memory/curator/report")).status_code == 404
    assert services.memory is not None
    fid, _ = await services.memory.add_fact("old rumour about the tape library")
    services.memory.store.db.execute(
        "UPDATE facts SET created_at = created_at - 100*86400 WHERE id=?", (fid,)
    )
    services.memory.store.db.commit()
    r = await client.post("/api/memory/curator/run", params={"dry_run": "true"})
    assert r.status_code == 200
    rep = r.json()
    assert rep["dry_run"] is True and rep["archived"] == [fid] and rep["skipped"] is None
    assert (await services.memory.get_fact(fid) or {})["archived_at"] is None
    r = await client.post("/api/memory/curator/run", params={"force": "true"})
    assert r.json()["archived"] == [fid] and r.json()["dry_run"] is False
    assert (await services.memory.get_fact(fid) or {})["archived_at"] is not None
    r = await client.post("/api/memory/curator/run")  # interval guard now applies
    assert r.json()["skipped"] and "last pass" in r.json()["skipped"]
    r = await client.get("/api/memory/curator/report")
    assert r.status_code == 200 and "REPORT.md" in r.json()["path"]
    assert "# Curator report" in r.json()["markdown"]
