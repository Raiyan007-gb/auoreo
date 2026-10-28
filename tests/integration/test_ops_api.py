"""W0-B ops endpoints: /api/doctor, /api/audit(+verify), /api/memory/facts (GET), /api/memory/files."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings

SECRET = "sk-proj-abcdefghijklmnopqrstuvwxyz0123"


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def test_doctor_json_shape(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    r = await client.get("/api/doctor")
    assert r.status_code == 200
    body = r.json()
    names = [c["check"] for c in body["checks"]]
    for want in ("python", "sqlite", "home", "harness", "credentials", "port", "auth", "audit", "yolo"):
        assert want in names, want
    assert all(set(c) == {"check", "level", "detail", "remedy"} for c in body["checks"])
    assert all(c["level"] in {"ok", "warn", "error"} for c in body["checks"])
    s = body["summary"]
    assert s["ok"] + s["warn"] + s["error"] == len(body["checks"])
    port = next(c for c in body["checks"] if c["check"] == "port")
    assert port["level"] == "ok" and "serving" in port["detail"]
    assert SECRET not in r.text


async def test_audit_list_filters_and_verify(client: httpx.AsyncClient, services: Services) -> None:
    for i in range(5):
        await services.audit.record(
            actor="user",
            action="tool.call" if i % 2 else "permission.decide",
            session_id="ses_a" if i < 3 else "ses_b",
            decision="allow",
            detail={"i": i},
        )
    r = await client.get("/api/audit")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert [x["seq"] for x in rows] == sorted(x["seq"] for x in rows) and len(rows) >= 5
    page = (await client.get("/api/audit", params={"limit": 2})).json()
    assert len(page["rows"]) == 2 and page["next_after_seq"] == page["rows"][-1]["seq"]
    nxt = (await client.get("/api/audit", params={"after_seq": page["next_after_seq"], "limit": 2})).json()
    assert nxt["rows"][0]["seq"] == page["rows"][-1]["seq"] + 1
    only = (await client.get("/api/audit", params={"session_id": "ses_b"})).json()["rows"]
    assert {x["session_id"] for x in only} == {"ses_b"} and len(only) == 2
    tool = (await client.get("/api/audit", params={"action": "tool.call"})).json()["rows"]
    assert {x["action"] for x in tool} == {"tool.call"}
    newest = (await client.get("/api/audit", params={"newest_first": "true", "limit": 1})).json()["rows"]
    assert newest[0]["seq"] == max(x["seq"] for x in rows)
    assert (await client.get("/api/audit", params={"limit": 501})).status_code == 422
    v = (await client.get("/api/audit/verify")).json()
    assert v == {"ok": True, "first_break_seq": None, "rows": len(rows)}


async def test_audit_detail_is_redacted(client: httpx.AsyncClient, services: Services) -> None:
    await services.audit.record(actor="user", action="probe")
    raw = sqlite3.connect(services.settings.db_path)
    try:
        raw.execute("DROP TRIGGER IF EXISTS audit_no_update")
        raw.execute("DROP TRIGGER IF EXISTS audit_no_delete")
        raw.execute("UPDATE audit SET detail=? WHERE action='probe'", (f'{{"key": "{SECRET}"}}',))
        raw.commit()
    finally:
        raw.close()
    r = await client.get("/api/audit", params={"action": "probe"})
    assert r.status_code == 200 and SECRET not in r.text


async def test_audit_verify_reports_tampering(client: httpx.AsyncClient, services: Services) -> None:
    for i in range(3):
        await services.audit.record(actor="user", action="tamper.test", detail={"i": i})
    listed = (await client.get("/api/audit", params={"action": "tamper.test"})).json()["rows"]
    seqs = [x["seq"] for x in listed]
    raw = sqlite3.connect(services.settings.db_path)
    try:
        raw.execute("DROP TRIGGER IF EXISTS audit_no_update")
        raw.execute("DROP TRIGGER IF EXISTS audit_no_delete")
        raw.execute("UPDATE audit SET decision='deny' WHERE seq=?", (seqs[1],))
        raw.commit()
    finally:
        raw.close()
    v = (await client.get("/api/audit/verify")).json()
    assert v["ok"] is False and v["first_break_seq"] == seqs[1]
    doc = (await client.get("/api/doctor")).json()
    audit = next(c for c in doc["checks"] if c["check"] == "audit")
    assert audit["level"] == "error" and str(seqs[1]) in audit["detail"]
    assert doc["summary"]["error"] >= 1


async def test_memory_facts_list(client: httpx.AsyncClient) -> None:
    ids = []
    texts = ("The staging DB lives on host db-stage-2", "Prefers tabs over spaces", "Coffee: oat flat white")
    for text in texts:
        ids.append((await client.post("/api/memory/facts", json={"content": text})).json()["id"])
    page = (await client.get("/api/memory/facts")).json()
    assert page["total"] == 3 and [f["id"] for f in page["facts"]] == list(reversed(ids))
    assert all(f["archived"] is False and f["source"] == "user" for f in page["facts"])
    hit = (await client.get("/api/memory/facts", params={"query": "STAGING"})).json()
    assert [f["id"] for f in hit["facts"]] == [ids[0]]
    await client.post(f"/api/memory/facts/{ids[1]}/pin", json={"pinned": True})
    pinned = (await client.get("/api/memory/facts", params={"pinned": "true"})).json()
    assert [f["id"] for f in pinned["facts"]] == [ids[1]] and pinned["facts"][0]["pinned"] is True
    await client.post(f"/api/memory/facts/{ids[2]}/forget")
    active = (await client.get("/api/memory/facts")).json()
    assert ids[2] not in [f["id"] for f in active["facts"]] and active["total"] == 2
    every = (await client.get("/api/memory/facts", params={"include_archived": "true"})).json()
    gone = next(f for f in every["facts"] if f["id"] == ids[2])
    assert gone["archived"] is True and gone["archived_at"] is not None
    paged = (await client.get("/api/memory/facts", params={"limit": 1, "offset": 1})).json()
    assert paged["total"] == 2 and [f["id"] for f in paged["facts"]] == [ids[0]]


async def test_memory_files_are_redacted(client: httpx.AsyncClient, settings: Settings) -> None:
    d = settings.home / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / "MEMORY.md").write_text(f"# Memory\n\n- deploy key: {SECRET}\n", encoding="utf-8")
    (d / "USER.md").write_text("# User\n\nLikes terse answers.\n", encoding="utf-8")
    r = await client.get("/api/memory/files")
    assert r.status_code == 200 and SECRET not in r.text
    files = {f["name"]: f for f in r.json()["files"]}
    assert set(files) == {"MEMORY.md", "USER.md", "PREFS.md", "PROJECT.md"}
    assert files["MEMORY.md"]["exists"] and "# Memory" in files["MEMORY.md"]["content"]
    assert "terse" in files["USER.md"]["content"]


async def test_welcome_events_are_audited(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/welcome/event", json={"step": "doctor", "action": "completed"})
    assert r.status_code == 204
    await client.post("/api/welcome/event", json={"step": "wizard", "action": "skipped"})
    rows = (await client.get("/api/audit", params={"newest_first": "true", "limit": 2})).json()["rows"]
    assert [(x["action"], x["detail"]) for x in rows] == [
        ("welcome.skipped", {"step": "wizard"}),
        ("welcome.completed", {"step": "doctor"}),
    ]
    bad = await client.post("/api/welcome/event", json={"step": "nope", "action": "completed"})
    assert bad.status_code == 422


async def test_ops_endpoints_require_auth(services: Services, settings: Settings) -> None:
    settings.server_password = "s3cret-pass"
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        paths = ("/api/doctor", "/api/audit", "/api/audit/verify", "/api/memory/facts", "/api/memory/files")
        for path in paths:
            assert (await c.get(path)).status_code == 401, path
        ok = await c.get("/api/audit/verify", headers={"Authorization": "Bearer s3cret-pass"})
        assert ok.status_code == 200


async def test_doctor_fixes_plan_then_apply(client: httpx.AsyncClient, settings: Settings) -> None:
    missing = settings.home / "skills"
    if missing.exists():
        missing.rmdir()
    plan = (await client.get("/api/doctor/fixes")).json()["fixes"]
    assert {
        "kind": "mkdir",
        "target": str(missing),
        "detail": "create missing runtime directory (0700)",
    } in plan
    done = (await client.post("/api/doctor/fixes")).json()
    assert done["failed"] == [] and any(f["target"] == str(missing) for f in done["applied"])
    assert missing.is_dir()
    assert all(f["target"] != str(missing) for f in (await client.get("/api/doctor/fixes")).json()["fixes"])
    rows = (await client.get("/api/audit", params={"action": "doctor.fix.mkdir"})).json()["rows"]
    assert any(r["detail"].get("target") == str(missing) for r in rows)
