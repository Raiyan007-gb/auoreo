"""Webhook ingress extras (spec §15, §19.11): the per-route IP allowlist and the `script:`
payload transform.

- `client_ip`: the TCP peer, unless the peer is a trusted proxy (`teams2.webhook_trusted_proxies`),
  in which case the right-most `X-Forwarded-For` hop that is not itself a trusted proxy.
- `run_script`: `<home>/scripts/<script>` (path traversal and symlink escapes refused) runs
  without a shell, cwd = the scripts dir, a scrubbed env, the payload as JSON on stdin; stdout
  must be one JSON object (the new payload) or `null` (ignore this delivery). Timeout →
  killed. stdout is capped at 1 MiB; stderr is only logged (redacted).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auoreo.core.redaction import redact
from auoreo.core.secrets import child_env

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.harness.manifests import WebhookManifest

log = logging.getLogger(__name__)
STDOUT_CAP = 1_048_576
IGNORE = object()


class ScriptError(Exception):
    pass


def _networks(cidrs: list[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    out = []
    for cidr in cidrs:
        with contextlib.suppress(ValueError):
            out.append(ipaddress.ip_network(cidr, strict=False))
    return out


def ip_in(ip: str, cidrs: list[str]) -> bool:
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return False
    return any(addr in net for net in _networks(cidrs))


def client_ip(peer: str | None, headers: Mapping[str, str], trusted: list[str]) -> str:
    ip = peer or ""
    if not trusted or not ip_in(ip, trusted):
        return ip
    hops = [h.strip() for h in headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        if not ip_in(hop, trusted):
            return hop
    return hops[0] if hops else ip


def scripts_dir(services: Services) -> Path:
    return Path(services.settings.home) / "scripts"


def script_path(services: Services, name: str) -> Path:
    root = scripts_dir(services).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ScriptError(f"script {name!r} escapes {root}")
    if not path.is_file():
        raise ScriptError(f"script {name!r} not found under {root}")
    return path


async def run_script(services: Services, manifest: WebhookManifest, payload: dict[str, Any]) -> Any:
    """The transformed payload (dict), or `IGNORE` when the script printed `null`."""
    assert manifest.script is not None
    path = script_path(services, manifest.script)
    argv = [sys.executable, "-I", str(path)] if path.suffix == ".py" else [str(path)]
    timeout = manifest.script_timeout_s or services.settings.teams2.webhook_script_timeout_s
    env = child_env([], services.secrets)
    env.update(AUOREO_WEBHOOK_ROUTE=manifest.name)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(path.parent),
            env=env,
        )
    except OSError as exc:
        raise ScriptError(f"cannot start {manifest.script}: {exc}") from exc
    try:
        out, err = await asyncio.wait_for(proc.communicate(json.dumps(payload).encode()), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise ScriptError(f"{manifest.script} timed out after {timeout:.0f}s") from None
    if err:
        log.info("webhook %s script stderr: %s", manifest.name, redact(err.decode("utf-8", "replace")[-500:]))
    if proc.returncode != 0:
        raise ScriptError(f"{manifest.script} exited with {proc.returncode}")
    if len(out) > STDOUT_CAP:
        raise ScriptError(f"{manifest.script} printed more than {STDOUT_CAP} bytes")
    try:
        result = json.loads(out.decode("utf-8") or "null")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScriptError(f"{manifest.script} did not print JSON") from exc
    if result is None:
        return IGNORE
    if not isinstance(result, dict):
        raise ScriptError(f"{manifest.script} must print a JSON object or null")
    return result


async def transform(services: Services, manifest: WebhookManifest, payload: dict[str, Any]) -> Any:
    """`script:` then `transform` hooks (match key `webhook:<route>`); `IGNORE` drops it."""
    if manifest.script:
        payload = await run_script(services, manifest, payload)
        if payload is IGNORE:
            return IGNORE
    from auoreo.runtime.hooks import hooks_of

    hooks = hooks_of(services)
    if hooks is None:
        return payload
    out = await hooks.transform(f"webhook:{manifest.name}", payload)
    return IGNORE if out is None else out
