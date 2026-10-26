"""I1 owner-only key files (§24.9 L881, §25.14 L1060): 0600 from creation + atomic rename,
owner/permission check on read (refuse a foreign owner, repair loose bits), and a
`schema_version` in the secrets.db payload (legacy flat maps still read)."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from auoreo.core import owner_files
from auoreo.core.crypto_columns import DbKeyring
from auoreo.core.owner_files import OwnerOnlyError, read_owner_only, write_owner_only
from auoreo.core.secrets import SECRETS_SCHEMA_VERSION, SecretStore, SecretStoreError

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("auoreo.core.secrets._keyring_get", lambda name: None)
    monkeypatch.setattr("auoreo.core.secrets._keyring_set", lambda name, value: False)
    monkeypatch.setattr("auoreo.core.crypto_columns._keyring_get", lambda name: None)
    monkeypatch.setattr("auoreo.core.crypto_columns._keyring_set", lambda name, value: False)


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


@posix_only
def test_write_is_0600_and_atomic(tmp_path: Path) -> None:
    target = tmp_path / "k"
    old = os.umask(0)
    try:
        write_owner_only(target, b"one")
        write_owner_only(target, b"two")
    finally:
        os.umask(old)
    assert target.read_bytes() == b"two" and _mode(target) == 0o600
    assert not list(tmp_path.glob("*.tmp"))  # no temp file left behind


@posix_only
def test_read_repairs_loose_bits_and_refuses_a_foreign_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    target = tmp_path / "k"
    target.write_bytes(b"secret")
    target.chmod(0o644)
    caplog.set_level(logging.WARNING, logger="auoreo.core.owner_files")
    assert read_owner_only(target) == b"secret"
    assert _mode(target) == 0o600 and "repairing to 0600" in caplog.text
    me = os.geteuid()
    monkeypatch.setattr(owner_files.os, "geteuid", lambda: me + 1)
    with pytest.raises(OwnerOnlyError, match="owned by uid"):
        read_owner_only(target)


@posix_only
def test_secret_store_files_are_owner_only(tmp_path: Path) -> None:
    store = SecretStore(tmp_path, "allow-file")
    store.set("A", "1")
    assert _mode(store.path) == 0o600 and _mode(store.key_file) == 0o600
    store.path.chmod(0o640)
    store.key_file.chmod(0o604)
    assert store.get("A") == "1"
    assert _mode(store.path) == 0o600 and _mode(store.key_file) == 0o600


def test_secrets_payload_has_schema_version_and_legacy_still_reads(tmp_path: Path) -> None:
    store = SecretStore(tmp_path, "allow-file")
    store.set("A", "1")
    key = store.key_file.read_bytes().strip()
    payload = json.loads(Fernet(key).decrypt(store.path.read_bytes()))
    assert payload == {"schema_version": SECRETS_SCHEMA_VERSION, "entries": {"A": "1"}}
    # legacy (pre-I1) flat map
    store.path.write_bytes(Fernet(key).encrypt(json.dumps({"OLD": "x"}).encode()))
    assert store.names() == ["OLD"] and store.get("OLD") == "x"
    # a newer schema refuses
    newer = {"schema_version": SECRETS_SCHEMA_VERSION + 1, "entries": {}}
    store.path.write_bytes(Fernet(key).encrypt(json.dumps(newer).encode()))
    with pytest.raises(SecretStoreError, match="newer"):
        store.names()


@posix_only
def test_db_key_file_is_owner_only(tmp_path: Path) -> None:
    ring = DbKeyring(tmp_path, "allow-file")
    keys = ring.ensure()
    assert _mode(ring.key_file) == 0o600
    ring.key_file.chmod(0o666)
    assert ring.load() == keys and _mode(ring.key_file) == 0o600
