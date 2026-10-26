"""Third-audit follow-ups: minted credentials are never idempotency-cached, the public share
view redacts even when redaction is switched off, and the key/trust files sit on the floors."""

from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.api.deps import IDEMPOTENT
from auoreo.core import floors_secrets, permissions, redaction
from auoreo.core.redaction import redact_obj

KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ"


def _route(router, path: str):
    full = f"{router.prefix}{path}"
    return next(r for r in router.routes if getattr(r, "path", None) == full and "POST" in r.methods)


def test_credential_minting_routes_are_not_idempotency_cached() -> None:
    from auoreo.api.routers.livekit import router as lk
    from auoreo.api.routers.rtc import router as rtc

    for router, path in ((lk, "/api/livekit/token"), (rtc, "/join")):  # lk has no prefix
        route = _route(router, path)
        assert IDEMPOTENT not in route.dependencies, path  # a cached token would be replayed for 24 h
    assert IDEMPOTENT in _route(rtc, "/offer").dependencies  # an SDP answer is fine to replay


def test_redact_obj_force_ignores_the_global_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redaction, "ENABLED", False)
    raw = {"messages": [{"content": f"key {KEY}"}]}
    assert redact_obj(raw) == raw  # the switch is honoured by default …
    forced = redact_obj(raw, force=True)  # … but the public share view forces it
    assert KEY not in str(forced) and "sk-ant" in str(forced)


def test_shared_view_forces_redaction() -> None:
    src = Path(permissions.__file__).with_name("sessions_more.py").read_text()
    assert "redact_obj(messages, force=True)" in src


def test_key_files_are_on_the_secret_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("AUOREO_HOME", str(home))
    for name in (".db.key", ".secrets.key", "secrets.db"):
        assert floors_secrets.scan(["cat", str(home / name)], [], f"cat {home / name}"), name
        assert floors_secrets.is_secret_path(str(home / name)), name
    assert not floors_secrets.scan(["cat", str(home / "settings.json")], [], "cat settings.json")


def test_trust_and_key_files_are_sensitive_write_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("AUOREO_HOME", str(home))
    for name in ("trusted_workspaces.json", ".db.key", ".secrets.key", "settings.json"):
        assert permissions.is_sensitive_write(str(home / name)), name
    assert not permissions.is_sensitive_write(str(tmp_path / "proj" / "notes.md"))
