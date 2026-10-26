"""Age-encrypted secret backups (spec §25.14): `auoreo secrets export|import`.

Export decrypts `secrets.db` in memory into `{schema_version, exported_at, entries[]}` and
encrypts it to every `--recipient` (age X25519 `age1…` or SSH public keys) with `pyrage`
(optional extra `age`, imported lazily). The Fernet key is never part of the payload: only
store entries are, and the backup is useless without one of the recipients' identities.
The receipt is `SHA256` over the sorted recipients (`secrets_export{recipients_hash}`).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
INSTALL_HINT = (
    "age encryption needs the optional `pyrage` package: `uv sync --extra age` "
    "(or `pip install 'auoreo[age]'` / `pip install pyrage`)"
)


class BackupError(RuntimeError):
    pass


def _pyrage() -> Any:
    try:
        import pyrage
    except ImportError as exc:
        raise BackupError(INSTALL_HINT) from exc
    return pyrage


def recipients_hash(recipients: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(r.strip() for r in recipients)).encode()).hexdigest()


def _recipient(pyrage: Any, text: str) -> Any:
    text = text.strip()
    try:
        if text.startswith("age1"):
            return pyrage.x25519.Recipient.from_str(text)
        if text.startswith(("ssh-ed25519 ", "ssh-rsa ")):
            return pyrage.ssh.Recipient.from_str(text)
    except Exception as exc:
        raise BackupError(f"invalid recipient {text[:12]}…: {exc}") from exc
    raise BackupError(f"unsupported recipient {text[:12]}… (expected age1… or an ssh-ed25519/ssh-rsa key)")


def _identities(pyrage: Any, path: Path) -> list[Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BackupError(f"cannot read identity file {path}: {exc}") from exc
    out: list[Any] = []
    for line in lines:
        line = line.strip()
        if line.startswith("AGE-SECRET-KEY-"):
            out.append(pyrage.x25519.Identity.from_str(line))
    if not out:
        text = "\n".join(lines)
        if "OPENSSH PRIVATE KEY" in text:
            out.append(pyrage.ssh.Identity.from_buffer(text.encode()))
    if not out:
        raise BackupError(f"{path} holds no AGE-SECRET-KEY-… or OpenSSH private key")
    return out


def payload(entries: dict[str, str]) -> bytes:
    doc = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "entries": [{"name": n, "value": v} for n, v in sorted(entries.items())],
    }
    return json.dumps(doc, indent=2).encode()


def encrypt(entries: dict[str, str], recipients: list[str]) -> bytes:
    if not recipients:
        raise BackupError("at least one --recipient is required")
    pyrage = _pyrage()
    keys = [_recipient(pyrage, r) for r in recipients]
    return bytes(pyrage.encrypt(payload(entries), keys))


def decrypt(blob: bytes, identity: Path) -> dict[str, str]:
    pyrage = _pyrage()
    try:
        raw = pyrage.decrypt(blob, _identities(pyrage, identity))
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError(f"cannot decrypt the backup with {identity}: {exc}") from exc
    return parse(bytes(raw))


def parse(raw: bytes) -> dict[str, str]:
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise BackupError("backup payload is not JSON") from exc
    if not isinstance(doc, dict) or int(doc.get("schema_version", 0)) > SCHEMA_VERSION:
        raise BackupError("unsupported backup schema_version (newer than this auoreo)")
    entries = doc.get("entries")
    if not isinstance(entries, list):
        raise BackupError("backup has no entries[]")
    return {str(e["name"]): str(e["value"]) for e in entries if isinstance(e, dict) and "name" in e}
