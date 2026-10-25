"""CLI: `auoreo memory forget|purge|pin|unpin|feedback|show` and `auoreo curator run|report`."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.cli.app import app


@pytest.fixture
def cli(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("AUOREO_GATEWAY", "fake")
    return CliRunner()


def add(cli: CliRunner, text: str) -> int:
    res = cli.invoke(app, ["memory", "add", text])
    assert res.exit_code == 0, res.output
    m = re.search(r"fact (\d+) added", res.output)
    assert m, res.output
    return int(m.group(1))


def show(cli: CliRunner, fid: int) -> dict:
    res = cli.invoke(app, ["memory", "show", str(fid)])
    assert res.exit_code == 0, res.output
    return json.loads(res.output)


def test_memory_lifecycle_commands(cli: CliRunner) -> None:
    fid = add(cli, "the build server is called hal")
    assert show(cli, fid)["source"] == "user"
    assert "pinned" in cli.invoke(app, ["memory", "pin", str(fid)]).output
    assert show(cli, fid)["pinned"] == 1
    assert "unpinned" in cli.invoke(app, ["memory", "unpin", str(fid)]).output
    res = cli.invoke(app, ["memory", "feedback", str(fid), "--unhelpful"])
    assert res.exit_code == 0 and "trust 0.40" in res.output
    assert cli.invoke(app, ["memory", "feedback", str(fid)]).exit_code != 0  # flag required
    res = cli.invoke(app, ["memory", "forget", str(fid)])
    assert res.exit_code == 0 and show(cli, fid)["archived_at"] is not None
    assert cli.invoke(app, ["memory", "forget", str(fid)]).exit_code == 1  # already archived
    res = cli.invoke(app, ["memory", "purge", str(fid)])
    assert res.exit_code == 2 and "--yes" in res.output
    assert show(cli, fid)["id"] == fid  # still there
    res = cli.invoke(app, ["memory", "purge", str(fid), "--yes"])
    assert res.exit_code == 0 and "purged" in res.output
    assert cli.invoke(app, ["memory", "show", str(fid)]).exit_code == 1
    assert cli.invoke(app, ["memory", "pin", "9999"]).exit_code == 1


def test_curator_commands(cli: CliRunner, home: Path) -> None:
    assert cli.invoke(app, ["curator", "report"]).exit_code == 1
    fid = add(cli, "old rumour about the fax machine")
    db = sqlite3.connect(home / "memory.db")
    db.execute("UPDATE facts SET created_at = created_at - 100*86400 WHERE id=?", (fid,))
    db.commit()
    db.close()
    res = cli.invoke(app, ["curator", "run", "--dry-run"])
    assert res.exit_code == 0 and f"would archive 1 [{fid}]" in res.output
    assert show(cli, fid)["archived_at"] is None
    res = cli.invoke(app, ["curator", "run", "--force", "--json"])
    rep = json.loads(res.output)
    assert rep["archived"] == [fid] and Path(rep["report_path"]).is_file()
    assert show(cli, fid)["archived_at"] is not None
    ledger = (home / "memory" / ".curator_ledger.jsonl").read_text().splitlines()
    assert json.loads(ledger[-1])["action"] == "archive"
    res = cli.invoke(app, ["curator", "run"])
    assert res.exit_code == 0 and "skipped" in res.output
    res = cli.invoke(app, ["curator", "report"])
    assert res.exit_code == 0 and "old rumour about the fax machine" in res.output
