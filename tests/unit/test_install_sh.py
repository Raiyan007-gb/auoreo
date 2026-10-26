"""I9 (spec §24.10 L893): `scripts/install.sh` against a local fake release directory —
signature + sha256 verification, refusal paths, pinned-key drift guard, shellcheck."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auoreo.core import upgrade as up

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "install.sh"
BINARY = b"#!/bin/sh\necho 9.9.9\n"

pytestmark = pytest.mark.skipif(
    not (shutil.which("openssl") and shutil.which("python3") and shutil.which("curl")),
    reason="installer needs openssl + python3 + curl",
)


def _pub(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def release(tmp: Path, key: Ed25519PrivateKey, *, channel: str = "stable", body: bytes = BINARY) -> Path:
    rel = tmp / "release"
    rel.mkdir(exist_ok=True)
    (rel / "auoreo-linux-x64").write_bytes(body)
    doc = {
        "version": "9.9.9",
        "channel": channel,
        "min_schema_version": 1,
        "assets": {
            "linux-x64": {
                "url": (rel / "auoreo-linux-x64").as_uri(),
                "sha256": hashlib.sha256(BINARY).hexdigest(),
            }
        },
    }
    (rel / "manifest-stable.json").write_text(json.dumps(up.sign_manifest(doc, key)), encoding="utf-8")
    return rel


def run(tmp: Path, rel: Path, pubkeys: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp),
        "AUOREO_MANIFEST_URL": (rel / "manifest-{channel}.json")
        .as_uri()
        .replace("%7B", "{")
        .replace("%7D", "}"),
        "AUOREO_PUBKEYS": pubkeys,
        "AUOREO_PLATFORM": "linux-x64",
        "AUOREO_INSTALL_DIR": str(tmp / "bin"),
    }
    return subprocess.run(["sh", str(SCRIPT), *args], env=env, capture_output=True, text=True, timeout=60)


def test_installs_a_verified_binary(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    res = run(tmp_path, release(tmp_path, key), f"bogus {_pub(key)}")
    assert res.returncode == 0, res.stderr
    target = tmp_path / "bin" / "auoreo"
    assert target.read_bytes() == BINARY and os.access(target, os.X_OK)
    assert "installed" in res.stderr and "add " in res.stderr  # PATH hint


def test_refuses_a_bad_signature(tmp_path: Path) -> None:
    rel = release(tmp_path, Ed25519PrivateKey.generate())
    res = run(tmp_path, rel, _pub(Ed25519PrivateKey.generate()))
    assert res.returncode != 0 and "signature does not verify" in res.stderr
    assert not (tmp_path / "bin" / "auoreo").exists()


def test_refuses_a_sha256_mismatch(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    rel = release(tmp_path, key, body=b"tampered")
    res = run(tmp_path, rel, _pub(key))
    assert res.returncode != 0 and "sha256 mismatch" in res.stderr
    assert not (tmp_path / "bin" / "auoreo").exists()


def test_refuses_a_channel_mismatch_and_bad_args(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    rel = release(tmp_path, key, channel="dev")
    res = run(tmp_path, rel, _pub(key))
    assert res.returncode != 0 and "channel" in res.stderr
    assert run(tmp_path, rel, _pub(key), "--channel", "nightly").returncode != 0


def test_pinned_key_matches_the_upgrade_module() -> None:
    m = re.search(r'^PINNED_PUBKEYS="([^"]+)"', SCRIPT.read_text(), re.M)
    assert m and m.group(1).split() == list(up.PINNED_PUBKEYS)
    assert up.DEFAULT_UPDATE_URL in SCRIPT.read_text().replace("{channel}", "{channel}")


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed (CI runs it)")
def test_shellcheck_clean() -> None:
    res = subprocess.run(["shellcheck", "--shell=sh", str(SCRIPT)], capture_output=True, text=True)
    assert res.returncode == 0, res.stdout
