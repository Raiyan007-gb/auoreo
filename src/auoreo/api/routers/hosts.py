"""`/api/hosts`: list, edit and check the SSH host inventory from the web (M7 hosts-c,
`m7.ssh.web`; 404 while it is off).

Same guard as `/api/env` (`ENV_AUTH`: server auth with the same-origin check, never a
capability token or a session JWT, bounded body). Writes are refused (403) when the request
arrives on the public tunnel host. Nothing here generates, deploys, rotates or reads a key,
or trusts a host key: a new address or port stays untrusted until `auoreo ssh test` at the
CLI, and the `ssh` grant key hashes the definition, so any edit asks again. Hosts are
re-read on every access (`Harness.hosts` compares a file stamp), so no reload is needed.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import ValidationError

from auoreo.api.deps import ServicesDep
from auoreo.api.routers.env_api import ENV_AUTH
from auoreo.api.security import _tunnel_host
from auoreo.core import host_keys, hosts
from auoreo.core.secrets import SecretStore
from auoreo.harness.manifests_hosts import HostManifest
from auoreo.schema.hosts import HostCheck, HostInfo, HostList, HostWrite

router = APIRouter(prefix="/api/hosts", tags=["hosts"], dependencies=ENV_AUTH)


def _enabled(svc: Any) -> None:
    if not getattr(svc.settings.m7.ssh, "web", False):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the hosts page is off (settings.m7.ssh.web)")


def _no_tunnel_writes(request: Request, svc: Any) -> None:
    tunnel = _tunnel_host(svc.settings.home)
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
    forwarded = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip().lower()
    if tunnel and tunnel.lower() in {host, forwarded.rsplit(":", 1)[0]}:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "host definitions cannot be changed over the public tunnel"
        )


def info(svc: Any, m: HostManifest, found: dict[str, HostManifest], names: set[str]) -> HostInfo:
    home = svc.settings.home
    secret = m.key_secret.removeprefix("secret:")
    known = hosts.known_keys(home, m.host, m.port)
    shadowed = m.name in svc.settings.environments
    problem = hosts.runtime_for(found, m).problem
    on = hosts.hosts_enabled(svc.settings)
    if problem is None and m.jump is not None and not hosts.jump_enabled(svc.settings):
        problem = "jump hosts are off (settings.m7.ssh.jump)"
    return HostInfo(
        name=m.name, host=m.host, port=m.port, user=m.user, key_name=secret,
        key_present=secret in names,
        key_fingerprint=(host_keys.read_keys(home).get(secret) or {}).get("fingerprint"),
        trusted=bool(known), host_key_fingerprints=[hosts.fingerprint_of(k) for k in known],
        jump=m.jump, tags=list(m.tags), audit_commands=m.audit_commands,
        keepalive_interval_s=m.keepalive_interval_s, description=m.description, shadowed=shadowed,
        usable_by_agents=on and bool(known) and not shadowed and problem is None, problem=problem,
    )  # fmt: skip


def _secret_names(svc: Any) -> set[str]:
    return set(SecretStore(svc.settings.home, getattr(svc.settings, "keyring", "allow-file")).names())


@router.get("", response_model=HostList)
async def list_hosts(svc: ServicesDep) -> HostList:
    """The host files of `<home>/hosts/` with trust and key state (no key material)."""
    _enabled(svc)
    found, problems = hosts.read_hosts(svc.settings.home)
    names = _secret_names(svc)
    rows = [info(svc, m, found, names) for m in found.values()]
    return HostList(hosts=rows, problems=problems, enabled=hosts.hosts_enabled(svc.settings))


_WRITE_BODY = {
    "required": True,
    "content": {
        "application/json": {
            "schema": HostWrite.model_json_schema(ref_template="#/components/schemas/{model}")
        }
    },
}


async def _body(request: Request) -> HostWrite:
    """The `HostWrite` body, validated here rather than by FastAPI so a 422 never echoes the
    submitted values back (a pasted private key must not travel in any response)."""
    try:
        data = await request.json()
        return HostWrite.model_validate(data)
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err["loc"]) or "body"
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{where}: {err['msg']}") from None
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "body: not JSON") from None


@router.put("/{name}", response_model=HostInfo, openapi_extra={"requestBody": _WRITE_BODY})
async def put_host(name: str, request: Request, svc: ServicesDep) -> HostInfo:
    """Create or replace `<home>/hosts/<name>.yaml` (body: `HostWrite`). Trust is never
    granted here."""
    _enabled(svc)
    _no_tunnel_writes(request, svc)
    body = await _body(request)
    home = svc.settings.home
    before = hosts.read_hosts(home)[0].get(name)
    fields = body.model_dump()
    fields["key_secret"] = f"secret:{fields.pop('key_name')}"
    try:
        hosts.write_host(home, name, fields, force=True)
    except ValueError as exc:  # the message never echoes a value
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    found = hosts.read_hosts(home)[0]
    m = found[name]
    old = before.model_dump() if before is not None else {}
    changed = sorted(k for k, v in m.model_dump().items() if old.get(k) != v) if before else ["created"]
    await svc.audit.record(
        actor="api", action="host.saved", decision="allow",
        detail={"name": name, "host": m.host, "port": m.port, "user": m.user,
                "key_name": m.key_secret.removeprefix("secret:"), "jump": m.jump, "tags": list(m.tags),
                "changed": changed},
    )  # fmt: skip
    return info(svc, m, found, _secret_names(svc))


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_host(name: str, request: Request, svc: ServicesDep) -> Response:
    """Delete the host file (never the secret or the known_hosts entry)."""
    _enabled(svc)
    _no_tunnel_writes(request, svc)
    home = svc.settings.home
    m = hosts.read_hosts(home)[0].get(name)
    try:
        gone = hosts.delete_host(home, name)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    if not gone or m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no host '{name}'")
    await svc.audit.record(
        actor="api", action="host.deleted", decision="allow",
        detail={"name": name, "host": m.host, "port": m.port, "user": m.user,
                "key_name": m.key_secret.removeprefix("secret:"), "jump": m.jump, "tags": list(m.tags),
                "changed": ["file"]},
    )  # fmt: skip
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{name}/check", response_model=HostCheck)
async def check_host(name: str, svc: ServicesDep) -> HostCheck:
    """Connect with the stored key and the trusted host key (attributed to the operator).
    Never probes or trusts an unknown host key: an untrusted host fails here."""
    _enabled(svc)
    from auoreo.tools import ssh_gate
    from auoreo.tools.environments import KIND, apply_overrides

    home = svc.settings.home
    found = hosts.read_hosts(home)[0]
    m = found.get(name)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no host '{name}'")
    ext = hosts.runtime_for(found, m)
    if ext.problem:
        return HostCheck(ok=False, detail=ext.problem)
    if not hosts.known_keys(home, m.host, m.port):
        return HostCheck(ok=False, detail=f"host key not trusted: run `auoreo ssh test {name}` at the CLI")
    bdef = svc.harness.select_backend(KIND, "ssh")
    if bdef is None:
        return HostCheck(ok=False, detail="the ssh environment backend is not available")
    backend = bdef.load()(apply_overrides(svc.settings, hosts.manifest_to_override(m, home)))
    backend.host_ext = ext
    ssh_gate.attach(backend, svc.settings, svc.audit.record, name, actor="operator", always=True)
    try:
        status_ = await asyncio.wait_for(backend.check(), 15)
        return HostCheck(ok=bool(status_.ok), detail=str(status_.detail)[:500])
    except TimeoutError:
        return HostCheck(ok=False, detail="timed out")
    finally:
        await backend.close()
