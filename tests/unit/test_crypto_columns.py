"""W1-E column encryption: events.data + audit.detail at rest, chain over ciphertext."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core import diagnostics
from auoreo.core.audit import AuditLog
from auoreo.core.bus import EventBus
from auoreo.core.crypto_columns import KEY_UNAVAILABLE, ColumnCipher, DbKeyring, is_envelope
from auoreo.core.db import Database
from auoreo.core.event_store import EventStore
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway

SECRET = "the-plaintext-marker-42"


def _cipher(home: Path, mode: str = "columns") -> ColumnCipher:
    return ColumnCipher(mode, DbKeyring(home))


async def _open(home: Path, mode: str = "columns") -> Database:
    db = Database(home / "state.db", cipher=_cipher(home, mode))
    await db.init()
    return db


def _raw(home: Path, sql: str) -> list[tuple[object, ...]]:
    con = sqlite3.connect(home / "state.db")
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


async def test_round_trip_and_ciphertext_at_rest(home: Path) -> None:
    db = await _open(home)
    store, audit = EventStore(db, EventBus()), AuditLog(db)
    ev = await store.append("s1", "message.delta", {"text": SECRET})
    assert ev.data == {"text": SECRET}  # the live event (bus) is plaintext
    await audit.record(actor="user", action="tool.call", detail={"cmd": SECRET})
    (replayed,) = await store.replay()
    assert replayed.data == {"text": SECRET}
    raw_event = _raw(home, "SELECT data FROM events")[0][0]
    raw_audit = _raw(home, "SELECT detail, actor, action FROM audit")[0]
    assert SECRET not in str(raw_event) and SECRET not in str(raw_audit[0])
    assert is_envelope(json.loads(str(raw_event))) and is_envelope(json.loads(str(raw_audit[0])))
    assert raw_audit[1:] == ("user", "tool.call")  # query columns stay plaintext
    assert (home / ".db.key").stat().st_mode & 0o777 == 0o600
    await db.close()


async def test_chain_verifies_over_ciphertext_without_key(home: Path) -> None:
    db = await _open(home)
    audit = AuditLog(db)
    for i in range(3):
        await audit.record(actor="a", action="x", detail={"i": i, "s": SECRET})
    assert await audit.verify() is None
    await db.close()
    (home / ".db.key").unlink()  # the key is gone: verify still works, reads degrade
    db2 = Database(home / "state.db", cipher=_cipher(home, "off"))
    await db2.init()
    audit2 = AuditLog(db2)
    assert await audit2.verify() is None
    async with db2.session() as s:
        from sqlalchemy import select

        from auoreo.core.db import AuditRow

        row = (await s.execute(select(AuditRow).limit(1))).scalar_one()
    assert audit2.plain_detail(row) == KEY_UNAVAILABLE
    await db2.close()


async def test_mixed_rows_and_disable_after_enable(home: Path) -> None:
    db = await _open(home, "off")
    await EventStore(db, EventBus()).append("s", "t", {"n": "plain"})
    await db.close()
    db = await _open(home, "columns")
    await EventStore(db, EventBus()).append("s", "t", {"n": "secret"})
    await db.close()
    db = await _open(home, "off")  # switched off again: the key still exists → readable
    await EventStore(db, EventBus()).append("s", "t", {"n": "plain2"})
    assert [e.data["n"] for e in await EventStore(db, EventBus()).replay()] == ["plain", "secret", "plain2"]
    stored = [json.loads(str(r[0])) for r in _raw(home, "SELECT data FROM events ORDER BY seq")]
    assert [is_envelope(d) for d in stored] == [False, True, False]
    await db.close()


async def test_missing_or_wrong_key_does_not_crash(home: Path, tmp_path: Path) -> None:
    db = await _open(home)
    await EventStore(db, EventBus()).append("s", "t", {"n": SECRET})
    await db.close()
    (home / ".db.key").write_text(_cipher(tmp_path).keyring.ensure()[0].decode())  # wrong key
    db = await _open(home, "off")
    (ev,) = await EventStore(db, EventBus()).replay()
    assert ev.data == KEY_UNAVAILABLE
    await db.close()
    st = Settings(home=home, gateway="fake", db_encryption="columns")
    rows = {c.check: c for c in await diagnostics.run_checks(st)}
    assert rows["db_encryption"].level == "warn" and "sample decrypt=failed" in rows["db_encryption"].detail


async def test_rotation_keeps_old_rows_readable(home: Path) -> None:
    db = await _open(home)
    store = EventStore(db, EventBus())
    await store.append("s", "t", {"k": 1})
    old = db.cipher.keyring.load()
    keys = db.cipher.keyring.rotate()
    assert len(keys) == 2 and keys[1:] == old
    await store.append("s", "t", {"k": 2})  # stale cache: still encrypts with the old primary
    db.cipher.invalidate()
    await store.append("s", "t", {"k": 3})  # new primary
    assert [e.data["k"] for e in await store.replay()] == [1, 2, 3]
    only_new = ColumnCipher("columns", DbKeyring(home))
    only_new.keyring.save(keys[:1])
    last = json.loads(str(_raw(home, "SELECT data FROM events ORDER BY seq DESC")[0][0]))
    assert only_new.decrypt(last) == {"k": 3}
    await db.close()


async def test_audit_triggers_still_block_update_delete(home: Path) -> None:
    db = await _open(home)
    await AuditLog(db).record(actor="a", action="x", detail={"s": SECRET})
    await db.close()
    con = sqlite3.connect(home / "state.db")
    for sql in ("UPDATE audit SET detail = '{}'", "DELETE FROM audit"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute(sql)
    con.close()


# ------------------------------------------------------------------ through the API
@pytest.fixture
async def enc_client(
    home: Path, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[httpx.AsyncClient, Services]]:
    monkeypatch.setenv("AUOREO_DB_ENCRYPTION", "columns")
    st = Settings(home=home, gateway="fake")
    assert st.db_encryption == "columns"
    svc = await Services.create(st, cwd=workdir, gateway=FakeGateway(), with_mcp=False)
    app = create_app(st, services=svc)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c, svc
    await svc.close()


async def test_sse_and_audit_api_return_plaintext(
    enc_client: tuple[httpx.AsyncClient, Services], home: Path
) -> None:
    client, svc = enc_client
    await svc.emit("s-enc", "note", {"text": SECRET})
    await svc.audit.record(actor="user", action="probe", detail={"text": SECRET})
    raw = " ".join(str(r) for r in _raw(home, "SELECT data FROM events"))
    assert SECRET not in raw and SECRET not in str(_raw(home, "SELECT detail FROM audit"))
    r = await client.get("/api/events", params={"session_id": "s-enc", "follow": "false"})
    assert SECRET in r.text
    page = (await client.get("/api/audit", params={"action": "probe"})).json()
    assert page["rows"][0]["detail"] == {"text": SECRET}
    assert (await client.get("/api/audit/verify")).json()["ok"] is True
    doctor = {c["check"]: c for c in (await client.get("/api/doctor")).json()["checks"]}
    assert doctor["db_encryption"]["level"] == "ok"
    assert "sample decrypt=ok" in doctor["db_encryption"]["detail"]
