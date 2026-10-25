"""F5 TURN REST API credentials (coturn `use-auth-secret`): the algorithm against a vector computed
independently with `openssl dgst -sha1 -hmac … -binary | base64`, TTL, secret resolution, the
`iceServers` shape and the doctor `rtc:turn` row."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from auoreo.core.diagnostics_rtc import turn_check
from auoreo.core.settings import Settings
from auoreo.rtc.turn import ephemeral_configured, ice_servers, turn_credentials

SECRET = "s3cr3t-shared"
# printf '%s' "1700003600:alice" | openssl dgst -sha1 -hmac "s3cr3t-shared" -binary | base64
VECTOR = "iLiwY1eZFZWG02u3lUgVz9kJLgU="


def test_known_vector() -> None:
    user, cred, exp = turn_credentials(SECRET, "alice", 3600, now=1_700_000_000)
    assert (user, cred, exp) == ("1700003600:alice", VECTOR, 1_700_003_600)
    mac = hmac.new(SECRET.encode(), user.encode(), hashlib.sha1).digest()
    assert base64.b64decode(cred) == mac and len(mac) == 20


def test_ttl_and_validation() -> None:
    user, _, exp = turn_credentials(SECRET, "u", 60, now=100.9)
    assert exp == 160 and user == "160:u"
    for bad in ({"ttl_s": 0}, {"ttl_s": -5}):
        with pytest.raises(ValueError):
            turn_credentials(SECRET, "u", now=0, **bad)
    with pytest.raises(ValueError, match="':'"):
        turn_credentials(SECRET, "a:b", 60)


def test_settings_shape(home: Path) -> None:
    st = Settings(home=home)
    assert st.turn_secret_ref is None and st.turn_urls == [] and st.turn_ttl_s == 3600
    with pytest.raises(ValidationError):
        Settings(home=home, turn_secret_ref="plaintext-secret")
    with pytest.raises(ValidationError):
        Settings(home=home, turn_ttl_s=10)


def test_ice_servers_never_contain_the_secret(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    st = Settings(
        home=home,
        rtc_ice_servers=["stun:stun.example.com:3478"],
        turn_secret_ref="secret:TURN_SECRET",
        turn_urls=["turns:turn.example.com:5349", "turn:turn.example.com:3478?transport=udp"],
        turn_ttl_s=600,
    )
    servers, exp = ice_servers(st, now=1_000)
    assert servers == [{"urls": ["stun:stun.example.com:3478"]}] and exp is None  # secret missing
    assert not ephemeral_configured(st)
    monkeypatch.setenv("TURN_SECRET", SECRET)
    servers, exp = ice_servers(st, now=1_000)
    assert exp == 1_600 and len(servers) == 2
    turn = servers[1]
    assert turn["urls"] == st.turn_urls and turn["username"] == "1600:auoreo"
    assert turn["credential"] == turn_credentials(SECRET, "auoreo", 600, now=1_000)[1]
    assert SECRET not in json.dumps(servers) and ephemeral_configured(st)


def test_doctor_turn_row(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    st = Settings(home=home, turn_secret_ref="secret:TURN_SECRET", turn_urls=["turns:t.example.com:5349"])
    row = turn_check(st, [])
    assert row.level == "warn" and "not set" in row.detail
    monkeypatch.setenv("TURN_SECRET", SECRET)
    row = turn_check(st, [])
    assert row.level == "ok" and "ephemeral TURN REST" in row.detail and SECRET not in row.detail
    assert turn_check(Settings(home=home, turn_urls=["turn:x"]), []).level == "warn"
    assert turn_check(Settings(home=home), ["turn:static"]).level == "ok"
    assert "deploy/coturn" in turn_check(Settings(home=home), []).remedy
