"""ed25519 signatures for bundled and registry plugins (spec §25.15 L1081, §26.14 trust tiers).

A signature covers `canonical_json({"name", "version", "sha256"})` where `sha256` is the
plugin's `folder_hash` (bundled) or the pinned distribution hash (registry). Bundled plugins
ship `plugins/SIGNATURES.json`, written by `packaging/sign_plugins.py`:

    {"version": 1, "plugins": {"telegram": {"version": "0.1.0", "sha256": "…",
                                            "signed_by": "<key id>", "sig": "<base64>"}}}

Keys are raw 32-byte ed25519 public keys (base64). `PLUGIN_PUBKEYS` is pinned in the repo;
`i6.plugins.extra_pubkeys` adds self-hoster/tap keys (a malformed one is ignored, never
weakening the pinned set). The key id is the first 16 hex chars of `sha256(raw key)`.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from auoreo.core.upgrade import canonical_json

PLUGIN_PUBKEYS: tuple[str, ...] = ("GR6bwC9YP1AyWLTfGvBlKjo0T5xrcD0lNrI/onhiED8=",)
"""Pinned plugin-signing keys. The matching private key is held by the release owner (the
dev key's PEM is never committed); rotate by appending the new key one release ahead."""
SIGNATURES_FILE = "SIGNATURES.json"


@dataclass(frozen=True)
class SigCheck:
    ok: bool
    signed_by: str | None
    reason: str


def key_id(raw_b64: str) -> str:
    return hashlib.sha256(base64.b64decode(raw_b64)).hexdigest()[:16]


def public_b64(key: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives import serialization

    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def trusted_keys(extra: list[str] | None = None) -> dict[str, Ed25519PublicKey]:
    out: dict[str, Ed25519PublicKey] = {}
    for b64 in (*PLUGIN_PUBKEYS, *(extra or [])):
        try:
            out[key_id(b64)] = Ed25519PublicKey.from_public_bytes(base64.b64decode(b64, validate=True))
        except ValueError:
            continue
    return out


def payload(name: str, version: str, sha256: str) -> bytes:
    return canonical_json({"name": name, "version": version, "sha256": sha256})


def sign_entry(name: str, version: str, sha256: str, key: Ed25519PrivateKey) -> dict[str, str]:
    sig = base64.b64encode(key.sign(payload(name, version, sha256))).decode()
    return {"version": version, "sha256": sha256, "signed_by": key_id(public_b64(key)), "sig": sig}


def verify_entry(
    name: str, version: str, sha256: str, entry: dict[str, Any] | None, extra: list[str] | None = None
) -> SigCheck:
    """Does `entry` (a SIGNATURES.json row or a tap index row) sign exactly this plugin?"""
    if not entry or not isinstance(entry.get("sig"), str):
        return SigCheck(False, None, "unsigned")
    if entry.get("sha256") != sha256:
        return SigCheck(False, None, "content changed since it was signed (sha256 mismatch)")
    if str(entry.get("version", "")) != version:
        return SigCheck(False, None, f"signed for version {entry.get('version')!r}, not {version!r}")
    try:
        raw = base64.b64decode(entry["sig"], validate=True)
    except ValueError:
        return SigCheck(False, None, "signature is not base64")
    keys = trusted_keys(extra)
    wanted = entry.get("signed_by")
    order = [wanted] if isinstance(wanted, str) and wanted in keys else []
    for kid in [*order, *(k for k in keys if k not in order)]:
        try:
            keys[kid].verify(raw, payload(name, version, sha256))
            return SigCheck(True, kid, "signed")
        except InvalidSignature:
            continue
    return SigCheck(False, None, "signature does not match any pinned plugin key")


def read_signatures(plugins_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads((plugins_dir / SIGNATURES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = data.get("plugins") if isinstance(data, dict) else None
    return {k: v for k, v in rows.items() if isinstance(v, dict)} if isinstance(rows, dict) else {}


def write_signatures(plugins_dir: Path, rows: dict[str, dict[str, Any]]) -> Path:
    path = plugins_dir / SIGNATURES_FILE
    body = {"version": 1, "plugins": dict(sorted(rows.items()))}
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


__all__ = [
    "PLUGIN_PUBKEYS",
    "SIGNATURES_FILE",
    "SigCheck",
    "key_id",
    "public_b64",
    "read_signatures",
    "sign_entry",
    "trusted_keys",
    "verify_entry",
    "write_signatures",
]
