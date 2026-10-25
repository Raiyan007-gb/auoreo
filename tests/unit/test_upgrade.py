"""`auoreo upgrade`: signed manifests, verification refusals, binary swap, rollback, receipts."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from auoreo import __version__
from auoreo.core import upgrade as up

URL = "https://updates.test/manifest-{channel}.json"
ASSET_URL = "https://updates.test/auoreo-linux-x64"
NEW = b"#!/bin/sh\necho new-auoreo\n"


def _b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> Ed25519PrivateKey:
    k = Ed25519PrivateKey.generate()
    monkeypatch.setattr(up, "PINNED_PUBKEYS", (_b64(k),))
    monkeypatch.setattr(up, "platform_key", lambda: "linux-x64")
    return k


def manifest(
    key: Ed25519PrivateKey, *, version: str = "9.0.0", sha: str | None = None, **over: Any
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "version": version,
        "channel": "stable",
        "min_schema_version": 1,
        "assets": {"linux-x64": {"url": ASSET_URL, "sha256": sha or hashlib.sha256(NEW).hexdigest()}},
    }
    doc.update(over)
    return up.sign_manifest(doc, key)


@pytest.fixture
def binary(tmp_path: Path) -> Path:
    b = tmp_path / "bin" / "auoreo"
    b.parent.mkdir()
    b.write_bytes(b"old-binary")
    b.chmod(0o755)
    return b


async def _run(home: Path, binary: Path, **kw: Any) -> up.Outcome:
    args: dict[str, Any] = {
        "home": home,
        "channel": "stable",
        "url_template": URL,
        "method": "binary",
        "binary": binary,
        "windows": False,
    }
    args.update(kw)
    return await up.run_upgrade(**args)


def _serve(httpx_mock: HTTPXMock, doc: dict[str, Any], asset: bytes | None = None) -> None:
    httpx_mock.add_response(url=URL.format(channel=doc.get("channel", "stable")), json=doc)
    if asset is not None:
        httpx_mock.add_response(url=ASSET_URL, content=asset)


def _read(path: Path) -> bytes:
    return path.read_bytes()


def _receipt(home: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((home / "upgrade" / "receipt.json").read_text())
    return data


# ------------------------------------------------------------------ good path + rollback
async def test_good_path_stages_swaps_keeps_prev_and_writes_receipt(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, home: Path, binary: Path
) -> None:
    _serve(httpx_mock, manifest(key), NEW)
    out = await _run(home, binary)
    assert out.status == "installed" and out.latest == "9.0.0"
    assert _read(binary) == NEW and os.access(binary, os.X_OK)
    assert _read(binary.parent / "auoreo.prev") == b"old-binary"
    assert not (binary.parent / "auoreo.new").exists()
    receipt = _receipt(home)
    assert receipt["action"] == "installed"
    assert receipt["from"] == __version__ and receipt["to"] == "9.0.0" and receipt["channel"] == "stable"
    assert receipt["sha256"] == hashlib.sha256(NEW).hexdigest() and receipt["ts"] > 0

    msg = up.rollback(home, binary)
    assert "restored" in msg
    assert _read(binary) == b"old-binary"
    assert _read(binary.parent / "auoreo.prev") == NEW  # roll forward stays possible
    assert _receipt(home)["action"] == "rollback"


def test_rollback_without_prev_refuses(home: Path, binary: Path) -> None:
    with pytest.raises(up.UpgradeError, match="nothing to roll back"):
        up.rollback(home, binary)


async def test_windows_stages_and_never_swaps(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, home: Path, tmp_path: Path
) -> None:
    exe = tmp_path / "auoreo.exe"
    exe.write_bytes(b"old-exe")
    _serve(httpx_mock, manifest(key), NEW)
    out = await _run(home, exe, windows=True)
    assert out.status == "staged" and "move /Y" in out.message and "auoreo.prev.exe" in out.message
    assert _read(exe) == b"old-exe"
    assert _read(tmp_path / "auoreo.new.exe") == NEW
    assert _receipt(home)["action"] == "staged"
    (tmp_path / "auoreo.prev.exe").write_bytes(b"older-exe")
    assert "move /Y" in up.rollback(home, exe, windows=True)
    assert _read(exe) == b"old-exe"  # Windows rollback only prints the swap


async def test_check_only_reports_without_downloading(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, home: Path, binary: Path
) -> None:
    _serve(httpx_mock, manifest(key))  # no asset registered: a download would fail the test
    out = await _run(home, binary, check_only=True)
    assert (out.status, out.current, out.latest) == ("available", __version__, "9.0.0")
    assert _read(binary) == b"old-binary"


async def test_up_to_date(key: Ed25519PrivateKey, httpx_mock: HTTPXMock, home: Path, binary: Path) -> None:
    _serve(httpx_mock, manifest(key, version=__version__))
    assert (await _run(home, binary)).status == "up-to-date"


async def test_pip_install_prints_the_command(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, home: Path, binary: Path
) -> None:
    _serve(httpx_mock, manifest(key))
    out = await _run(home, binary, method="uv-tool")
    assert out.status == "command" and out.message == "uv tool upgrade auoreo"
    assert _read(binary) == b"old-binary"


# ------------------------------------------------------------------ refusals
async def test_tampered_manifest_is_refused(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, home: Path, binary: Path
) -> None:
    doc = manifest(key)
    doc["assets"]["linux-x64"]["url"] = "https://evil.test/auoreo"
    _serve(httpx_mock, doc)
    with pytest.raises(up.UpgradeError, match="signature"):
        await _run(home, binary)
    assert _read(binary) == b"old-binary"


async def test_foreign_key_refused_unless_trusted(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, home: Path, binary: Path
) -> None:
    other = Ed25519PrivateKey.generate()
    doc = manifest(other)
    httpx_mock.add_response(url=URL.format(channel="stable"), json=doc, is_reusable=True)
    with pytest.raises(up.UpgradeError, match="pinned"):
        await _run(home, binary, check_only=True)
    out = await _run(home, binary, check_only=True, extra_pubkeys=["not-base64!", _b64(other)])
    assert out.status == "available"


async def test_unsigned_manifest_refused(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, home: Path, binary: Path
) -> None:
    doc = manifest(key)
    del doc["sig"]
    _serve(httpx_mock, doc)
    with pytest.raises(up.UpgradeError, match="not signed"):
        await _run(home, binary)


async def test_sha_mismatch_refused_and_cleaned_up(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, home: Path, binary: Path
) -> None:
    _serve(httpx_mock, manifest(key, sha="0" * 64), NEW)
    with pytest.raises(up.UpgradeError, match="sha256 mismatch"):
        await _run(home, binary)
    assert _read(binary) == b"old-binary"
    assert sorted(p.name for p in binary.parent.iterdir()) == ["auoreo"]
    assert not (home / "upgrade" / "receipt.json").exists()


async def test_downgrade_refused(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, home: Path, binary: Path
) -> None:
    _serve(httpx_mock, manifest(key, version="0.0.1"))
    with pytest.raises(up.UpgradeError, match="downgrade"):
        await _run(home, binary)


async def test_schema_gap_refused(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, home: Path, binary: Path
) -> None:
    _serve(httpx_mock, manifest(key, min_schema_version=10_000))
    with pytest.raises(up.UpgradeError, match="intermediate release"):
        await _run(home, binary)


async def test_channel_mismatch_and_missing_platform(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, home: Path, binary: Path
) -> None:
    httpx_mock.add_response(url=URL.format(channel="beta"), json=manifest(key))  # says stable
    with pytest.raises(up.UpgradeError, match="channel"):
        await _run(home, binary, channel="beta")
    _serve(httpx_mock, manifest(key, assets={"macos-arm64": {"url": ASSET_URL, "sha256": "0" * 64}}))
    with pytest.raises(up.UpgradeError, match="no asset for linux-x64"):
        await _run(home, binary)


async def test_unknown_channel(home: Path, binary: Path) -> None:
    with pytest.raises(up.UpgradeError, match="unknown channel"):
        await _run(home, binary, channel="nightly")


# ------------------------------------------------------------------ helpers
def test_versions_and_channels() -> None:
    order = ["1.0.0.dev1", "1.0.0a1", "1.0.0b2", "1.0.0rc1", "1.0.0", "1.0.1", "1.1.0"]
    assert sorted(order, key=up.parse_version) == order
    assert [up.channel_of(v) for v in ("1.0.0", "1.0.0rc1", "1.0.0b1", "1.0.0.dev3")] == [
        "stable",
        "beta",
        "beta",
        "dev",
    ]
    with pytest.raises(up.UpgradeError):
        up.parse_version("banana")


def test_upgrade_commands() -> None:
    assert up.upgrade_command("pipx", "stable") == "pipx upgrade auoreo"
    assert up.upgrade_command("uv-tool", "beta").endswith("--prerelease allow")
    assert up.upgrade_command("pip", "dev").endswith("install -U auoreo --pre")
    assert up.install_method() == "checkout"  # the test suite runs from the repo


def test_canonical_json_excludes_sig_and_is_stable() -> None:
    a = up.canonical_json({"b": 1, "a": {"y": 2, "x": 1}, "sig": "zzz"})
    assert a == b'{"a":{"x":1,"y":2},"b":1}'


# ------------------------------------------------------------------ CLI
def test_cli_check_and_command(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auoreo.cli.app import app

    monkeypatch.setenv("AUOREO_UPDATE_URL", URL)
    httpx_mock.add_response(url=URL.format(channel="stable"), json=manifest(key), is_reusable=True)
    r = CliRunner().invoke(app, ["upgrade", "--check"])
    assert r.exit_code == 0, r.output
    assert f"update available: {__version__} → 9.0.0" in r.output
    r = CliRunner().invoke(app, ["upgrade", "--yes"])
    assert r.exit_code == 0, r.output
    assert "git pull && uv sync" in r.output  # a checkout never swaps files


def test_cli_refusal_and_rollback_guard(
    key: Ed25519PrivateKey, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auoreo.cli.app import app

    monkeypatch.setenv("AUOREO_UPDATE_URL", URL)
    _serve(httpx_mock, manifest(key, version="0.0.1"))
    r = CliRunner().invoke(app, ["upgrade", "--check"])
    assert r.exit_code == 1 and "downgrade" in r.output
    r = CliRunner().invoke(app, ["upgrade", "--rollback", "--yes"])
    assert r.exit_code == 1 and "checkout install" in r.output
