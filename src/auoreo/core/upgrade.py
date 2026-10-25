"""Signed, channel-aware self-upgrade (spec L704/L868/L1096).

A release publishes one manifest per channel (`stable|beta|dev`):

    {"version": "0.2.0", "channel": "stable", "min_schema_version": 1,
     "assets": {"linux-x64": {"url": "...", "sha256": "..."}, ...}, "sig": "<base64>"}

`sig` is an ed25519 signature over `canonical_json(manifest without "sig")`, checked against
`PINNED_PUBKEYS` (plus `settings.update_pubkeys` for self-hosters) BEFORE anything else is read.

Refusals (each raises `UpgradeError`): bad/missing signature, a channel mismatch, no asset
for this platform, a sha256 mismatch, a downgrade (use `--rollback`), and a schema gap:
`min_schema_version` is the oldest state.db schema the new release can migrate FROM, so an
install whose `SCHEMA_VERSION` is below it must step through an intermediate release first
(the new build would otherwise refuse to open, or silently skip, the local database).

Frozen binaries download → verify → stage next to the binary → keep the current one as
`auoreo.prev` (N-1) → `os.replace` the new one in → write `<home>/upgrade/receipt.json`.
Windows cannot replace a running .exe: the new file is staged and the user is told how to
swap it after closing auoreo (never taskkill). pip/uv/pipx installs only print the command.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from auoreo import __version__

Channel = Literal["stable", "beta", "dev"]
CHANNELS: tuple[str, ...] = ("stable", "beta", "dev")

PINNED_PUBKEYS: tuple[str, ...] = ("dKWsJip0NPV09nDE/9z7qM7MCR/oY26gdzaKVtvzYgM=",)
"""Raw 32-byte ed25519 release keys (base64). The matching private key is the
`UPDATE_SIGNING_KEY` repository secret; rotate by appending the new key one release ahead."""

MIN_UPGRADE_SCHEMA = 1
"""Oldest state.db schema this build's Alembic chain migrates from (0001_baseline). The
release workflow publishes it as the manifest's `min_schema_version`."""

DEFAULT_UPDATE_URL = (
    "https://github.com/Raiyan007-gb/auoreo/releases/download/updates/manifest-{channel}.json"
)
_PRE_RANK = {"dev": 0, "a": 1, "alpha": 1, "b": 2, "beta": 2, "rc": 3}
_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:[-.]?(dev|a|alpha|b|beta|rc)\.?(\d*))?$")


class UpgradeError(Exception):
    """A refusal: nothing was installed."""


@dataclass(frozen=True)
class Asset:
    url: str
    sha256: str


@dataclass(frozen=True)
class Manifest:
    version: str
    channel: str
    min_schema_version: int
    assets: dict[str, Asset] = field(default_factory=dict)


# ------------------------------------------------------------------ signing
def canonical_json(doc: dict[str, Any]) -> bytes:
    """The signed bytes: sorted keys, no whitespace, UTF-8, `sig` excluded."""
    body = {k: v for k, v in doc.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sign_manifest(doc: dict[str, Any], key: Ed25519PrivateKey) -> dict[str, Any]:
    return {**doc, "sig": base64.b64encode(key.sign(canonical_json(doc))).decode()}


def _pubkeys(extra: list[str] | None) -> list[Ed25519PublicKey]:
    keys = []
    for b64 in (*PINNED_PUBKEYS, *(extra or [])):
        try:
            keys.append(Ed25519PublicKey.from_public_bytes(base64.b64decode(b64)))
        except ValueError:
            continue  # a malformed self-hoster key never weakens the pinned one
    return keys


def verify_manifest(doc: dict[str, Any], extra_pubkeys: list[str] | None = None) -> Manifest:
    """Signature first, then shape. Raises `UpgradeError`."""
    sig = doc.get("sig")
    if not isinstance(sig, str):
        raise UpgradeError("manifest is not signed")
    try:
        raw_sig = base64.b64decode(sig, validate=True)
    except ValueError as exc:
        raise UpgradeError("manifest signature is not base64") from exc
    payload = canonical_json(doc)
    for key in _pubkeys(extra_pubkeys):
        try:
            key.verify(raw_sig, payload)
            break
        except InvalidSignature:
            continue
    else:
        raise UpgradeError("manifest signature does not match any pinned release key")
    try:
        assets = {p: Asset(str(a["url"]), str(a["sha256"]).lower()) for p, a in doc["assets"].items()}
        return Manifest(str(doc["version"]), str(doc["channel"]), int(doc["min_schema_version"]), assets)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise UpgradeError(f"malformed manifest: {exc}") from exc


# ------------------------------------------------------------------ versions & platform
def parse_version(v: str) -> tuple[int, int, int, int, int]:
    """`1.2.3` > `1.2.3rc1` > `1.2.3b2` > `1.2.3a1` > `1.2.3.dev4` (enough PEP 440 for releases)."""
    m = _VERSION.match(v.strip())
    if not m:
        raise UpgradeError(f"unparseable version {v!r}")
    major, minor, patch, pre, num = m.groups()
    rank = _PRE_RANK[pre] if pre else 9
    return int(major), int(minor), int(patch), rank, int(num or 0)


def channel_of(version: str) -> Channel:
    rank = parse_version(version)[3]
    return "stable" if rank == 9 else "dev" if rank == 0 else "beta"


def platform_key() -> str:
    """`<os>-<arch>[-musl]`, e.g. `linux-x64`, `macos-arm64`, `windows-x64`."""
    os_name = {"darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    musl = os_name == "linux" and platform.libc_ver()[0] != "glibc" and Path("/etc/alpine-release").exists()
    return f"{os_name}-{arch}" + ("-musl" if musl else "")


def install_method() -> str:
    """binary | checkout | uv-tool | pipx | pip."""
    if getattr(sys, "frozen", False):
        return "binary"
    if (Path(__file__).resolve().parents[3] / "pyproject.toml").is_file():
        return "checkout"
    prefix = Path(sys.prefix).as_posix()
    if "/uv/tools/" in prefix:
        return "uv-tool"
    if "/pipx/venvs/" in prefix:
        return "pipx"
    return "pip"


def upgrade_command(method: str, channel: str) -> str:
    pre = channel != "stable"
    commands = {
        "uv-tool": "uv tool upgrade auoreo" + (" --prerelease allow" if pre else ""),
        "pipx": "pipx upgrade auoreo" + (" --pip-args=--pre" if pre else ""),
        "pip": f"{sys.executable} -m pip install -U auoreo" + (" --pre" if pre else ""),
        "checkout": "git pull && uv sync",
    }
    return commands.get(method, commands["pip"])


# ------------------------------------------------------------------ checks
def evaluate(m: Manifest, *, channel: str, current: str = __version__, schema: int | None = None) -> str:
    """`up-to-date` or `available`; raises `UpgradeError` for every refusal."""
    if m.channel != channel:
        raise UpgradeError(f"manifest is for channel {m.channel!r}, not {channel!r}")
    if schema is None:
        from auoreo.core.db import SCHEMA_VERSION

        schema = SCHEMA_VERSION
    new, cur = parse_version(m.version), parse_version(current)
    if new == cur:
        return "up-to-date"
    if new < cur:
        raise UpgradeError(f"{m.version} is older than {current}: refusing to downgrade (use --rollback)")
    if m.min_schema_version > schema:
        raise UpgradeError(
            f"{m.version} migrates state.db from schema v{m.min_schema_version}+, this install is at "
            f"v{schema}: upgrade through an intermediate release first"
        )
    return "available"


def manifest_url(template: str, channel: str) -> str:
    return template.replace("{channel}", channel)


async def fetch_manifest(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    try:
        r = await client.get(url)
        r.raise_for_status()
        doc = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise UpgradeError(f"cannot fetch manifest {url}: {exc}") from exc
    if not isinstance(doc, dict):
        raise UpgradeError("manifest is not a JSON object")
    return doc


def _discard(path: Path) -> None:
    path.unlink(missing_ok=True)


async def download(client: httpx.AsyncClient, asset: Asset, dest: Path) -> str:
    """Stream to `dest`, verify sha256, delete on mismatch. Returns the digest."""
    h = hashlib.sha256()
    try:
        async with client.stream("GET", asset.url) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                async for chunk in r.aiter_bytes():
                    h.update(chunk)
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        _discard(dest)
        raise UpgradeError(f"download failed: {exc}") from exc
    if h.hexdigest() != asset.sha256:
        _discard(dest)
        raise UpgradeError(f"sha256 mismatch for {asset.url}: got {h.hexdigest()}, want {asset.sha256}")
    return h.hexdigest()


# ------------------------------------------------------------------ install
def prev_path(binary: Path) -> Path:
    return binary.with_name(binary.stem + ".prev" + binary.suffix)


def write_receipt(home: Path, fields: dict[str, Any]) -> Path:
    """`<home>/upgrade/receipt.json` {action, from, to, channel, sha256, ts} (atomic)."""
    path = home / "upgrade" / "receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({**fields, "ts": time.time()}, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def read_receipt(home: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((home / "upgrade" / "receipt.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def swap(binary: Path, staged: Path) -> Path:
    """POSIX: keep the current binary as N-1, then atomically replace it. Returns prev."""
    prev = prev_path(binary)
    tmp_prev = binary.with_name(f".{binary.name}.prev-tmp")
    shutil.copy2(binary, tmp_prev)
    os.replace(tmp_prev, prev)
    staged.chmod(0o755)
    os.replace(staged, binary)
    return prev


def windows_instructions(binary: Path, staged: Path) -> str:
    prev = prev_path(binary)
    return (
        f"Staged {staged.name}. Close every running auoreo (the server and any CLI), then run:\n"
        f'  move /Y "{binary}" "{prev}" && move /Y "{staged}" "{binary}"'
    )


def rollback(home: Path, binary: Path, *, windows: bool = False) -> str:
    """Swap `auoreo.prev` back in; the current binary becomes the new `.prev` (roll forward)."""
    prev = prev_path(binary)
    if not prev.is_file():
        raise UpgradeError(f"nothing to roll back to: {prev} does not exist")
    last = read_receipt(home) or {}
    if windows:
        tmp = binary.with_name(binary.stem + ".cur" + binary.suffix)
        return (
            "Close every running auoreo, then run:\n"
            f'  move /Y "{binary}" "{tmp}" && move /Y "{prev}" "{binary}" && move /Y "{tmp}" "{prev}"'
        )
    tmp = binary.with_name(f".{binary.name}.rollback-tmp")
    shutil.copy2(binary, tmp)
    os.replace(prev, binary)
    os.replace(tmp, prev)
    to = last.get("from", "previous") if last.get("action") == "installed" else "previous"
    write_receipt(home, {"action": "rollback", "from": __version__, "to": to, "channel": last.get("channel")})
    return f"restored {binary} from {prev.name} (state.db written by a newer schema may refuse to open)"


@dataclass
class Outcome:
    status: str  # up-to-date | available | installed | staged | command
    current: str
    latest: str
    message: str = ""
    receipt: Path | None = None


async def run_upgrade(
    *,
    home: Path,
    channel: str,
    url_template: str = DEFAULT_UPDATE_URL,
    extra_pubkeys: list[str] | None = None,
    check_only: bool = False,
    method: str | None = None,
    binary: Path | None = None,
    windows: bool | None = None,
    client: httpx.AsyncClient | None = None,
) -> Outcome:
    """Fetch + verify + evaluate; then install (binary), stage (Windows) or describe (pip)."""
    if channel not in CHANNELS:
        raise UpgradeError(f"unknown channel {channel!r} (stable|beta|dev)")
    method = method or install_method()
    own = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30, read=300))
    try:
        m = verify_manifest(await fetch_manifest(client, manifest_url(url_template, channel)), extra_pubkeys)
        status = evaluate(m, channel=channel)
        out = Outcome(status, __version__, m.version)
        if status == "up-to-date" or check_only:
            return out
        if method != "binary":
            out.status, out.message = "command", upgrade_command(method, channel)
            return out
        key = platform_key()
        asset = m.assets.get(key)
        if asset is None:
            raise UpgradeError(f"release {m.version} has no asset for {key}")
        binary = binary or Path(sys.executable)
        windows = sys.platform == "win32" if windows is None else windows
        staged = binary.with_name(binary.stem + ".new" + binary.suffix)
        digest = await download(client, asset, staged)
    finally:
        if own:
            await client.aclose()
    receipt = {"from": __version__, "to": m.version, "channel": channel, "sha256": digest}
    if windows:
        out.status, out.message = "staged", windows_instructions(binary, staged)
        out.receipt = write_receipt(home, {"action": "staged", **receipt})
        return out
    prev = swap(binary, staged)
    out.status, out.message = "installed", f"{binary} → {m.version} (previous kept as {prev.name})"
    out.receipt = write_receipt(home, {"action": "installed", **receipt})
    return out
