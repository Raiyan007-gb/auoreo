"""Data at rest (spec L1069): the `auoreo-db-key` key ring and column-level encryption.

`db_encryption = "columns"` stores `events.data` and `audit.detail` as
`{"enc": "v1", "ct": "<Fernet token>"}`; every other column (ids, types, seq, actor, action,
timestamps) stays plaintext so queries keep working. The audit hash chain is computed over
the *stored* envelope, so `AuditLog.verify()` never needs the key.

Every reader goes through `ColumnCipher.decrypt()` (then redaction). Plaintext rows (written
before encryption was enabled) pass through unchanged, and envelopes stay readable after it
is switched off as long as the key exists. A missing or wrong key yields
`{"enc": "v1", "error": "key unavailable"}` instead of an exception.

Key ring: newest key first, comma-separated, in the OS keyring under `auoreo/auoreo-db-key`;
without a usable keyring (and `keyring != "forbid-file"`) in a 0600 `.db.key` file next to
`state.db`. `auoreo db rotate-key` prepends a new primary (`MultiFernet`); old keys are kept
because audit ciphertext is immutable (UPDATE is blocked by trigger and would break the chain).
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from auoreo.core.owner_files import read_owner_only, write_owner_only
from auoreo.core.secrets import _keyring_get, _keyring_set

if TYPE_CHECKING:
    from auoreo.core.diagnostics import Check
    from auoreo.core.services import Services
    from auoreo.core.settings import Settings

log = logging.getLogger(__name__)
Mode = Literal["off", "columns", "sqlcipher"]
KEY_NAME = "auoreo-db-key"
KEY_FILE = ".db.key"
ENC_VERSION = "v1"
KEY_UNAVAILABLE: dict[str, Any] = {"enc": ENC_VERSION, "error": "key unavailable"}


class DbKeyError(RuntimeError):
    pass


def parse_keys(raw: str | bytes) -> list[bytes]:
    text = raw.decode() if isinstance(raw, bytes) else raw
    return [k.strip().encode() for k in text.replace("\n", ",").split(",") if k.strip()]


class DbKeyring:
    """The `auoreo-db-key` key list (newest first): OS keyring, else a 0600 file."""

    def __init__(self, home: Path, policy: str = "allow-file") -> None:
        self.key_file = home / KEY_FILE
        self.policy = policy

    def load(self) -> list[bytes]:
        if (k := _keyring_get(KEY_NAME)) is not None:
            return parse_keys(k)
        if self.policy != "forbid-file" and self.key_file.is_file():
            return parse_keys(read_owner_only(self.key_file))  # I1: owner-only or refused
        return []

    def save(self, keys: list[bytes]) -> str:
        """Persist the list; returns where it went (`keyring` or the file path)."""
        value = ",".join(k.decode() for k in keys)
        in_keyring = _keyring_get(KEY_NAME) is not None or not self.key_file.is_file()
        if in_keyring and _keyring_set(KEY_NAME, value):
            return "keyring"
        if self.policy != "allow-file":
            raise DbKeyError("no usable OS keyring and AUOREO_KEYRING forbids a key file")
        write_owner_only(self.key_file, value.encode())  # 0600 from the first byte + rename
        return str(self.key_file)

    def ensure(self) -> list[bytes]:
        keys = self.load()
        if not keys:
            keys = [Fernet.generate_key()]
            where = self.save(keys)
            log.warning("created the database key (%s)", where)
        return keys

    def rotate(self) -> list[bytes]:
        keys = [Fernet.generate_key(), *self.load()]
        self.save(keys)
        return keys


class ColumnCipher:
    """Encrypt-on-write (only in `columns` mode) and decrypt-on-read (always)."""

    def __init__(self, mode: str, keyring: DbKeyring) -> None:
        self.mode = mode
        self.keyring = keyring
        self._lock = threading.Lock()
        self._mf: MultiFernet | None = None

    @property
    def encrypting(self) -> bool:
        return self.mode == "columns"

    def _fernet(self, *, create: bool = False, reload: bool = False) -> MultiFernet | None:
        with self._lock:
            if self._mf is None or reload:
                keys = self.keyring.ensure() if create else self.keyring.load()
                self._mf = MultiFernet([Fernet(k) for k in keys]) if keys else None
            return self._mf

    def ensure_key(self) -> None:
        if self._fernet(create=True) is None:
            raise DbKeyError("database key unavailable")

    def invalidate(self) -> None:
        with self._lock:
            self._mf = None

    def encrypt(self, data: dict[str, Any]) -> dict[str, Any]:
        if not self.encrypting:
            return data
        mf = self._fernet(create=True)
        if mf is None:
            raise DbKeyError("database key unavailable")
        token = mf.encrypt(json.dumps(data, separators=(",", ":"), default=str).encode())
        return {"enc": ENC_VERSION, "ct": token.decode()}

    def decrypt(self, stored: Any) -> dict[str, Any]:
        if not is_envelope(stored):
            return stored if isinstance(stored, dict) else {}
        token = str(stored["ct"]).encode()
        for reload in (False, True):  # a key rotated by another process: reload once
            mf = self._fernet(reload=reload)
            if mf is None:
                continue
            try:
                out = json.loads(mf.decrypt(token))
                return out if isinstance(out, dict) else {"value": out}
            except (InvalidToken, ValueError):
                continue
        return dict(KEY_UNAVAILABLE)


def is_envelope(value: Any) -> bool:
    return isinstance(value, dict) and value.get("enc") == ENC_VERSION and "ct" in value


def cipher_for(st: Settings, home: Path | None = None) -> ColumnCipher:
    return ColumnCipher(st.db_encryption, DbKeyring(home or st.home, st.keyring))


# ------------------------------------------------------------------ doctor (W1-E)
async def doctor_checks(st: Settings, services: Services | None = None) -> list[Check]:
    """`db_encryption` row (mode, key present, sample decrypt) + `sqlcipher` when selected."""
    import asyncio

    from auoreo.core.diagnostics import Check

    cipher = services.db.cipher if services is not None else cipher_for(st, st.db_path.parent)
    keys = await asyncio.to_thread(cipher.keyring.load)
    sample = await _sample_ok(st, services, cipher)
    detail = f"mode={st.db_encryption}; key={'present' if keys else 'absent'} ({len(keys)} in ring)"
    detail += f"; sample decrypt={sample}"
    level: Literal["ok", "warn", "error"] = "ok"
    remedy = ""
    if sample == "failed":
        level, remedy = "warn", "encrypted rows exist but the database key is missing or wrong"
    elif st.db_encryption == "columns" and not keys:
        level = "warn"
        remedy = "the key is created on first write; back it up with `auoreo db backup --with-key`"
    out = [Check("db_encryption", level, detail, remedy)]
    if st.db_encryption == "sqlcipher":
        from auoreo.core import sqlcipher

        ok, info = await asyncio.to_thread(sqlcipher.check)
        out.append(
            Check(
                "sqlcipher",
                "ok" if ok else "error",
                info,
                "" if ok else "install the `sqlcipher` extra or use db_encryption=columns",
            )
        )
    return out


async def _sample_ok(st: Settings, services: Services | None, cipher: ColumnCipher) -> str:
    """Decrypt the newest encrypted event, if any: `ok`, `failed` or `n/a`."""
    from sqlalchemy import func, select

    from auoreo.core.db import Database, EventRow

    if services is None and not st.db_path.is_file():
        return "n/a"
    db = services.db if services is not None else Database(st.db_path)
    try:
        async with db.session() as s:
            enc = func.json_extract(EventRow.__table__.c.data, "$.enc") == ENC_VERSION
            q = select(EventRow.data).where(enc).order_by(EventRow.seq.desc())
            stored = (await s.execute(q.limit(1))).scalar()
    except Exception:
        return "n/a"
    finally:
        if services is None:
            await db.close()
    if stored is None:
        return "n/a"
    return "failed" if "error" in cipher.decrypt(stored) else "ok"
