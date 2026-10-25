"""db_query: allow-listed DSNs, SELECT-only gate, row cap, redaction."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import ToolContext


@pytest.fixture
def gate(services: Services):  # type: ignore[no-untyped-def]
    fn = services.harness.tools["db_query"].handler()
    return sys.modules[fn.__module__].select_gate


@pytest.fixture
def demo_db(tmp_path: Path, services: Services) -> Path:
    path = tmp_path / "demo.db"
    with sqlite3.connect(path) as db:
        db.execute("create table t (id integer, name text, token text)")
        db.executemany(
            "insert into t values (?, ?, ?)",
            [
                (i, f"row{i}", "sk-or-v1-abcdefghijklmnopqrstuvwxyz0123456789" if i == 0 else "-")
                for i in range(500)
            ],
        )
    services.settings.db_query_dsns = {"demo": f"sqlite:///{path}", "vault": "secret:DEMO_DSN"}
    return path


async def call(services: Services, workdir: Path, args: dict) -> tuple[str, bool]:
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "db_query", json.dumps(args)),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine([]),
    )
    return out.result.content, out.result.is_error


@pytest.mark.parametrize(
    ("sql", "ok"),
    [
        ("select 1", True),
        ("SELECT * FROM t;", True),
        ("  -- leading comment\nselect id from t", True),
        ("with x as (select 1 as a) select a from x", True),
        ("select 1 /* comment */ as a", True),
        ("select 1; drop table t", False),
        ("select 1; select 2", False),
        ("drop table t", False),
        ("delete from t", False),
        ("insert into t values (1)", False),
        ("update t set name = 'x'", False),
        ("pragma table_info(t)", False),
        ("attach database 'x.db' as x", False),
        ("with x as (select 1) delete from t", False),
        ("select * into backup from t", False),
        ("create table z (a int)", False),
        ("alter table t add column z", False),
        ("select 1 -- ; drop table t", True),
        ("/* select */ drop table t", False),
        ("", False),
        ("explain select 1", False),
    ],
)
def test_select_gate(gate, sql: str, ok: bool) -> None:  # type: ignore[no-untyped-def]
    why, _ = gate(sql)
    assert (why is None) is ok, why


async def test_query_returns_markdown_table(services: Services, workdir: Path, demo_db: Path) -> None:
    text, err = await call(
        services, workdir, {"dsn_name": "demo", "sql": "select id, name from t where id < 3"}
    )
    assert not err, text
    assert text.splitlines()[:2] == ["| id | name |", "|---|---|"]
    assert "| 2 | row2 |" in text and "3 row(s)" in text


async def test_row_cap(services: Services, workdir: Path, demo_db: Path) -> None:
    text, err = await call(services, workdir, {"dsn_name": "demo", "sql": "select id from t", "max_rows": 10})
    assert not err and "10 row(s); capped at max_rows=10" in text
    text, _ = await call(services, workdir, {"dsn_name": "demo", "sql": "select id from t"})
    assert "200 row(s); capped" in text


async def test_values_are_redacted(services: Services, workdir: Path, demo_db: Path) -> None:
    text, err = await call(services, workdir, {"dsn_name": "demo", "sql": "select token from t where id = 0"})
    assert not err and "abcdefghijklmnop" not in text


async def test_refusals(services: Services, workdir: Path, demo_db: Path) -> None:
    text, err = await call(services, workdir, {"dsn_name": "demo", "sql": "select 1; drop table t"})
    assert err and "refused" in text
    with sqlite3.connect(demo_db) as db:
        assert db.execute("select count(*) from t").fetchone()[0] == 500
    text, err = await call(services, workdir, {"dsn_name": "nope", "sql": "select 1"})
    assert err and "allow-listed: demo, vault" in text
    text, err = await call(services, workdir, {"dsn_name": "vault", "sql": "select 1"})
    assert err and "DEMO_DSN" in text and "not set" in text


async def test_secret_dsn(
    services: Services, workdir: Path, demo_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEMO_DSN", f"sqlite:///{demo_db}")
    text, err = await call(services, workdir, {"dsn_name": "vault", "sql": "select count(*) as n from t"})
    assert not err and "| 500 |" in text, text
