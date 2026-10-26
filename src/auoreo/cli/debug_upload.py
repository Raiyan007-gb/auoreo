"""H4: `auoreo debug upload` (spec §18, §19.10 "debug upload redacted before egress").

Only ever sends to `cli2.debug_upload_url` (https, or http on loopback): no endpoint, no
upload. The archive is the same force-redacted bundle `debug bundle` writes; before egress
every member is scanned again (redaction gate) and any hit refuses the upload. The user
confirms the destination and size unless `--yes`. A bearer token, if the endpoint needs one,
comes from the secret NAMED by `cli2.debug_upload_secret` and is never printed."""

from __future__ import annotations

import asyncio
import json
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
import typer

from auoreo import __version__
from auoreo.cli.common import console, err_console
from auoreo.core import debuginfo
from auoreo.core.settings import Settings

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class UploadRefused(RuntimeError):
    pass


def check_endpoint(url: str | None) -> str:
    if not url:
        raise UploadRefused(
            "no upload endpoint configured (set cli2.debug_upload_url); nothing was sent. "
            "`auoreo debug bundle` writes the same redacted archive locally."
        )
    parts = urlparse(url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK):
        return url
    raise UploadRefused(
        f"refusing to upload to {parts.scheme}://{parts.hostname}: https (or loopback http) only"
    )


def _leaks(scrub: debuginfo.Scrubber, name: str, raw: bytes) -> bool:
    """Would forced redaction still change this member? JSON members are checked value by
    value (as `build_bundle` redacts them), everything else as text; a known secret value
    anywhere in the bytes is always a leak."""
    text = raw.decode("utf-8", errors="replace")
    if any(v in text for v in scrub.values):
        return True
    if name.endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError:
            return True
        return bool(scrub.obj(data) != data)
    return scrub.text(text) != text


def redaction_gate(st: Settings, archive: Path) -> None:
    """Re-scan every member before egress; any member redaction would still change is a leak."""
    scrub = debuginfo.Scrubber(st)
    with zipfile.ZipFile(archive) as zf:
        for name in zf.namelist():
            if _leaks(scrub, name, zf.read(name)):
                raise UploadRefused(
                    f"redaction gate: {name} still contains secret-looking text; not uploading"
                )


def _headers(st: Settings) -> dict[str, str]:
    headers = {"Content-Type": "application/zip", "User-Agent": f"auoreo/{__version__}"}
    name = st.cli2.debug_upload_secret
    if name:
        from auoreo.core.secrets import SecretStore

        token = SecretStore(st.home, st.keyring).get(name)
        if not token:
            raise UploadRefused(f"secret '{name}' (cli2.debug_upload_secret) is not set")
        headers["Authorization"] = f"Bearer {token}"
    return headers


def upload(st: Settings, *, yes: bool, keep: Path | None) -> None:
    try:
        url = check_endpoint(st.cli2.debug_upload_url)
        with tempfile.TemporaryDirectory() as tmp:
            archive = keep or Path(tmp) / "auoreo-debug.zip"
            members = asyncio.run(debuginfo.build_bundle(st, archive))
            redaction_gate(st, archive)
            size = archive.stat().st_size
            host = urlparse(url).hostname
            if not yes:
                typer.confirm(
                    f"Upload redacted debug bundle ({len(members)} files, {size} bytes) to {host}?",
                    abort=True,
                )
            resp = httpx.post(url, content=archive.read_bytes(), headers=_headers(st), timeout=60)
    except UploadRefused as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    except httpx.HTTPError as exc:
        err_console.print(f"[red]upload failed: {type(exc).__name__}: {exc}[/red]")
        raise typer.Exit(1) from exc
    if resp.status_code >= 400:
        err_console.print(f"[red]upload refused by {host}: HTTP {resp.status_code}[/red]")
        raise typer.Exit(1)
    ref = ""
    if resp.headers.get("content-type", "").startswith("application/json"):
        body = resp.json()
        ref = str(body.get("id") or body.get("url") or "") if isinstance(body, dict) else ""
    console.print(f"uploaded {size} bytes to {host} (HTTP {resp.status_code})" + (f": {ref}" if ref else ""))
