"""Sign the bundled plugins (`plugins/<name>/`) into `plugins/SIGNATURES.json`, or make a key.

    python packaging/sign_plugins.py keygen --out plugin-signing-key.pem
    PLUGIN_SIGNING_KEY="$(cat plugin-signing-key.pem)" python packaging/sign_plugins.py sign
    python packaging/sign_plugins.py sign --key plugin-signing-key.pem [--plugins-dir plugins]
    python packaging/sign_plugins.py verify          # exit 1 when any bundled plugin is unsigned/stale

Each row signs `{name, version, sha256}` where `sha256` is `auoreo.plugins.install.folder_hash`
of the plugin folder (venv, lock, caches and state files excluded). Commit only the public key
(`auoreo.plugins.signing.PLUGIN_PUBKEYS`) and `SIGNATURES.json`; never the private key.
Re-run `sign` after editing a bundled plugin.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auoreo.plugins.install import folder_hash
from auoreo.plugins.signing import public_b64, read_signatures, sign_entry, verify_entry, write_signatures

ROOT = Path(__file__).resolve().parents[1]


def keygen(out: Path) -> None:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    out.write_bytes(pem)
    out.chmod(0o600)
    print(f"private key: {out} (keep it out of the repository)")
    print(f"public key (add to PLUGIN_PUBKEYS): {public_b64(key)}")


def load_key(path: Path | None) -> Ed25519PrivateKey:
    pem = path.read_bytes() if path else os.environ.get("PLUGIN_SIGNING_KEY", "").strip().encode()
    if not pem:
        sys.exit("pass --key or set PLUGIN_SIGNING_KEY")
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit("the signing key is not an ed25519 private key")
    return key


def bundled(plugins_dir: Path) -> dict[str, tuple[str, str]]:
    """name → (version, folder sha256) for every `plugins/<name>/plugin.yaml`."""
    out: dict[str, tuple[str, str]] = {}
    for folder in sorted(p for p in plugins_dir.iterdir() if (p / "plugin.yaml").is_file()):
        if folder.name.startswith((".", "_")):
            continue
        meta = yaml.safe_load((folder / "plugin.yaml").read_text(encoding="utf-8")) or {}
        out[str(meta.get("name", folder.name))] = (str(meta.get("version", "")), folder_hash(folder))
    return out


def sign(plugins_dir: Path, key: Ed25519PrivateKey) -> Path:
    rows = {name: sign_entry(name, ver, sha, key) for name, (ver, sha) in bundled(plugins_dir).items()}
    path = write_signatures(plugins_dir, rows)
    print(f"signed {len(rows)} plugin(s) → {path}")
    return path


def verify(plugins_dir: Path) -> int:
    rows = read_signatures(plugins_dir)
    bad = 0
    for name, (ver, sha) in bundled(plugins_dir).items():
        check = verify_entry(name, ver, sha, rows.get(name))
        print(f"{name:12} {'ok' if check.ok else 'FAIL'}  {check.reason}")
        bad += not check.ok
    return 1 if bad else 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    k = sub.add_parser("keygen")
    k.add_argument("--out", type=Path, required=True)
    for name in ("sign", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--plugins-dir", type=Path, default=ROOT / "plugins")
        if name == "sign":
            p.add_argument("--key", type=Path, default=None)
    ns = ap.parse_args()
    if ns.cmd == "keygen":
        keygen(ns.out)
    elif ns.cmd == "sign":
        sign(ns.plugins_dir, load_key(ns.key))
    else:
        sys.exit(verify(ns.plugins_dir))


if __name__ == "__main__":
    main()
