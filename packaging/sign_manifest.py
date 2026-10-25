"""Write signed update manifests (`manifest-<channel>.json`) for a release, or make a key.

    python packaging/sign_manifest.py keygen --out update-signing-key.pem
    UPDATE_SIGNING_KEY="$(cat key.pem)" python packaging/sign_manifest.py build \\
        --version 0.2.0 --assets-dir binaries --base-url https://github.com/o/r/releases/download/v0.2.0 \\
        --out-dir manifests

`build` hashes every `auoreo-<platform>[.exe]` in `--assets-dir`, writes `SHA256SUMS` next to
them, and signs one manifest per channel the version belongs to: a stable release is offered on
stable, beta and dev; a beta (a/b/rc) on beta and dev; a dev build on dev only.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auoreo.core.upgrade import MIN_UPGRADE_SCHEMA, PINNED_PUBKEYS, channel_of, sign_manifest, verify_manifest

FANOUT = {"stable": ("stable", "beta", "dev"), "beta": ("beta", "dev"), "dev": ("dev",)}


def keygen(out: Path) -> None:
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    out.write_bytes(pem)
    out.chmod(0o600)
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    print(f"private key: {out} (store as the UPDATE_SIGNING_KEY secret)")
    print(f"public key (add to PINNED_PUBKEYS): {base64.b64encode(raw).decode()}")


def load_key() -> Ed25519PrivateKey:
    pem = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
    if not pem:
        sys.exit("UPDATE_SIGNING_KEY is not set")
    key = serialization.load_pem_private_key(pem.encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        sys.exit("UPDATE_SIGNING_KEY is not an ed25519 private key")
    return key


def build(version: str, assets_dir: Path, base_url: str, out_dir: Path) -> list[Path]:
    key = load_key()
    assets: dict[str, dict[str, str]] = {}
    sums: list[str] = []
    for f in sorted(assets_dir.iterdir()):
        if not f.is_file() or not f.name.startswith("auoreo-"):
            continue
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        sums.append(f"{digest}  {f.name}")
        assets[f.name.removeprefix("auoreo-").removesuffix(".exe")] = {
            "url": f"{base_url.rstrip('/')}/{f.name}",
            "sha256": digest,
        }
    if not assets:
        sys.exit(f"no auoreo-<platform> binaries in {assets_dir}")
    (assets_dir / "SHA256SUMS").write_text("\n".join(sums) + "\n", encoding="utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    if base64.b64encode(pub).decode() not in PINNED_PUBKEYS:
        print("warning: signing key is not in PINNED_PUBKEYS; installs will refuse it", file=sys.stderr)
    written = []
    for channel in FANOUT[channel_of(version)]:
        doc = {
            "version": version,
            "channel": channel,
            "min_schema_version": MIN_UPGRADE_SCHEMA,
            "assets": assets,
        }
        signed = sign_manifest(doc, key)
        verify_manifest(signed, [base64.b64encode(pub).decode()])  # round-trip before publishing
        path = out_dir / f"manifest-{channel}.json"
        path.write_text(json.dumps(signed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
        print(path)
    return written


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    kg = sub.add_parser("keygen")
    kg.add_argument("--out", type=Path, required=True)
    b = sub.add_parser("build")
    b.add_argument("--version", required=True)
    b.add_argument("--assets-dir", type=Path, required=True)
    b.add_argument("--base-url", required=True)
    b.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.cmd == "keygen":
        keygen(args.out)
    else:
        build(args.version.removeprefix("v"), args.assets_dir, args.base_url, args.out_dir)


if __name__ == "__main__":
    main()
