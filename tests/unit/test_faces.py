"""W2-C: faces are default-off, NC-gated for InsightFace, and embeddings are never persisted."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from auoreo.core.diagnostics import backend_checks
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.perception.faces import cosine, vault_for
from tests.conftest import write
from tests.perception_helpers import call, ctx_for, faces_png, write_png


@pytest.fixture
async def faces_on(home: Path, workdir: Path, gateway: FakeGateway) -> AsyncIterator[Services]:
    # project rule so the `ask`-by-default tool runs in non-interactive turns
    write(
        workdir / ".auoreo" / "permissions" / "faces.yaml",
        "rules:\n  - {permission: faces, pattern: '*', action: allow}\n",
    )
    svc = await Services.create(
        Settings(home=home, gateway="fake", faces_enabled=True), cwd=workdir, gateway=gateway, with_mcp=False
    )
    try:
        yield svc
    finally:
        await svc.close()


async def test_detect_faces_refused_while_disabled(services: Services, workdir: Path) -> None:
    write_png(workdir, "f.png", faces_png())
    res = await call(services, ctx_for(services, workdir), "detect_faces", {"image": "f.png"})
    assert res.is_error and "face detection is disabled" in res.content and "faces_enabled" in res.content


async def test_detect_faces_asks_by_default(faces_on: Services, workdir: Path) -> None:
    write_png(workdir, "f.png", faces_png())
    ctx = ctx_for(faces_on, workdir)
    ctx.services["engine"] = PermissionEngine([])
    res = await call(faces_on, ctx, "detect_faces", {"image": "f.png"})
    assert res.is_error and "needs approval" in res.content


async def test_detect_faces_boxes_only_by_default(faces_on: Services, workdir: Path) -> None:
    write_png(workdir, "f.png", faces_png())
    res = await call(faces_on, ctx_for(faces_on, workdir), "detect_faces", {"image": "f.png"})
    assert not res.is_error, res.content
    assert res.content.startswith("[faces: fake] 2 face(s)")
    assert "landmarks" not in res.content and "ref=" not in res.content and res.images == []
    assert set(res.meta["faces"][0]) == {"label", "score", "x0", "y0", "x1", "y1"}
    res = await call(
        faces_on,
        ctx_for(faces_on, workdir),
        "detect_faces",
        {"image": "f.png", "landmarks": True, "annotate": True},
    )
    assert res.content.count("landmarks=[[") == 2 and len(res.meta["faces"][0]["landmarks"]) == 5
    assert len(res.images) == 1


async def test_insightface_nc_gate(services: Services, home: Path) -> None:
    load = services.harness.backends_for("face")["insightface"].load()
    st = await load(Settings(home=home)).check()
    assert not st.ok and "face_nc_opt_in is false" in st.detail and "NON-COMMERCIAL" in st.detail
    assert "face_nc_opt_in" in st.remedy
    st = await load(Settings(home=home, face_nc_opt_in=True)).check()
    assert not st.ok and "not installed" in st.detail  # opt-in passes the gate; library absent here
    mp = await services.harness.backends_for("face")["mediapipe"].load()(Settings(home=home)).check()
    assert not mp.ok and "mediapipe" in mp.detail
    nc = Settings(home=home, backends={"face": "insightface"})
    rows = {c.check: c for c in await backend_checks(nc, services.harness)}
    assert "insightface [nc-opt-in]" in rows["backend:face"].detail
    assert "NON-COMMERCIAL" in rows["backend:face"].detail and rows["backend:face"].level == "warn"


def _dump(path: Path) -> str:
    if not path.exists():
        return ""
    con = sqlite3.connect(path)
    try:
        return "\n".join(con.iterdump())
    except sqlite3.DatabaseError:  # e.g. extension-backed virtual tables: fall back to raw bytes
        return path.read_bytes().decode("latin-1")
    finally:
        con.close()


async def test_embeddings_are_never_persisted(
    faces_on: Services, gateway: FakeGateway, workdir: Path, home: Path
) -> None:
    data = faces_png()
    write_png(workdir, "f.png", data)
    expected = [f.embedding for f in await faces_on.backend("face").faces(data, embeddings=True)]
    assert all(e and len(e) == 16 for e in expected)
    memory_before = _dump(home / "memory.db")
    gateway.queue(
        Scripted(tool_calls=[("tool_search", {"query": "detect faces", "limit": 3})]),
        Scripted(tool_calls=[("detect_faces", {"image": "f.png", "embeddings": True})]),
        Scripted(text="two faces"),
    )
    sess = await faces_on.create_session()
    await faces_on.coordinator.submit(sess.id, "who is in f.png", interactive=False)
    await faces_on.coordinator.wait(sess.id, 10)
    hist = await faces_on.coordinator.runner.load_history(sess.id)
    out = next(m["content"] for m in hist if m["role"] == "tool" and "[faces:" in str(m["content"]))
    assert out.count("ref=face_") == 2 and "similarity face_" in out and "never stored" in out
    vault = vault_for(faces_on)
    assert vault.count(sess.id) == 2  # held in memory only
    # nothing biometric reached the transcript, the events table, the audit log or memory
    state = _dump(faces_on.settings.db_path)
    assert state, "state.db should exist"
    for emb in expected:
        assert emb is not None
        for v in emb[:4]:
            assert repr(v) not in state and repr(v) not in out
    assert _dump(home / "memory.db") == memory_before
    await faces_on.emit(sess.id, "session.archived", {})  # forget on session close
    for _ in range(100):
        if vault.count(sess.id) == 0:
            break
        await asyncio.sleep(0.02)
    assert vault.count(sess.id) == 0


async def test_compare_to_and_forget(faces_on: Services, workdir: Path) -> None:
    write_png(workdir, "f.png", faces_png())
    ctx = ctx_for(faces_on, workdir)
    first = await call(faces_on, ctx, "detect_faces", {"image": "f.png", "embeddings": True})
    refs = [w.removeprefix("ref=") for w in first.content.split() if w.startswith("ref=")]
    assert len(refs) == 2
    again = await call(faces_on, ctx, "detect_faces", {"image": "f.png", "compare_to": refs[:1]})
    assert f"~{refs[0]}: 1.000" in again.content  # the same face matches itself
    gone = await call(
        faces_on, ctx, "detect_faces", {"image": "f.png", "forget": True, "compare_to": refs[:1]}
    )
    assert "unknown ref (expired or forgotten)" in gone.content
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
