"""I1 debug bundle (§24.9 L882, §25.10 L1021): event stats are counts not rows, PII is masked
(home prefix → `~`, login name, e-mails, non-loopback IPs) and the manifest carries the audit
`chain_head{seq, hash}`."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from auoreo.core import debuginfo
from auoreo.core.debuginfo import build_bundle, pii
from auoreo.core.services import Services
from auoreo.core.settings import Settings


def test_pii_masking_is_idempotent() -> None:
    text = (
        "cwd=/home/alice/proj user alice mail alice@example.com from 203.0.113.7 "
        "and fe80:0:0:0:1:2:3:4; local 127.0.0.1 ::1 alice_dir"
    )
    out = pii(text, "/home/alice", "alice")
    assert out == ("cwd=~/proj user <user> mail <email> from <ip> and <ip>; local 127.0.0.1 ::1 alice_dir")
    assert pii(out, "/home/alice", "alice") == out


async def test_bundle_has_counts_not_rows_pii_masked_and_chain_head(
    services: Services, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sid = (await services.create_session(title="t")).id
    await services.emit(sid, "custom.private", {"text": "my diary: the secret recipe"})
    await services.audit.record(actor="test", action="probe", session_id=sid)
    monkeypatch.setattr(debuginfo, "_home_prefix", lambda: str(settings.home.parent))
    monkeypatch.setattr(debuginfo, "_login_name", lambda: "zelda")
    (settings.home / "server.log").write_text(
        f"open {settings.home}/state.db for zelda <zelda@corp.example> peer 198.51.100.4\n"
    )
    out = tmp_path / "b.zip"
    await build_bundle(settings, out)
    with zipfile.ZipFile(out) as zf:
        blob = "\n".join(zf.read(n).decode() for n in zf.namelist())
        events = json.loads(zf.read("events.json"))
        manifest = json.loads(zf.read("manifest.json"))
        log = zf.read("logs/server.log").decode()
    assert "secret recipe" not in blob and "my diary" not in blob
    assert events["total"] >= 1 and events["by_type"]["custom.private"] == 1 and events["last_seq"]
    assert str(settings.home.parent) not in blob
    assert "zelda" not in blob and "198.51.100.4" not in blob and "corp.example" not in blob
    assert log.startswith("open ~/") and "<user>" in log and "<email>" in log and "<ip>" in log
    head = manifest["chain_head"]
    assert head["seq"] >= 1 and len(head["hash"]) == 64
