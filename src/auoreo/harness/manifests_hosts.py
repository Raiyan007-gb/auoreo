"""`hosts/<name>.yaml`: one SSH host of the inventory (M7 hosts-a), and its loader.

A host file says which stored key is offered to which address, so it is accepted from the
runtime root (`<home>/hosts/`) only: never from a project `.auoreo/` (trusted or not), an
`extra_roots` folder or the bundled definitions. It holds no key material: `key_secret` is a
`secret:<NAME>` reference into the secret store; public keys and fingerprints are not stored
(the server's host key lives in `<home>/ssh/known_hosts`, written only by `auoreo ssh test`).
"""

from __future__ import annotations

import ipaddress
import socket
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from auoreo.harness.manifests import NAME_RE, check_reserved

if TYPE_CHECKING:
    from auoreo.harness.discovery import Candidate

RUNTIME_ONLY = "hosts load from the runtime root only (<home>/hosts/); this file is ignored"

_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in ("169.254.0.0/16", "fe80::/10"))
_BLOCKED_ADDRS = frozenset(ipaddress.ip_address(a) for a in ("100.100.100.200", "0.0.0.0", "::"))


def unsafe_address(host: str) -> str | None:
    """Why `host` may not be an SSH target, or None. Only literals are checked: link-local and
    cloud-metadata addresses and the unspecified address. A hostname is resolved at connect
    time and not re-checked (known limitation, `docs/environments.md`). The numeric spellings a
    resolver reads as IPv4 without a lookup (`2852039166`, `0xa9fea9fe`, `169.254.43518`) count
    as literals."""
    text = host.strip().strip("[]").split("%", 1)[0]
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(text))
        except (OSError, ValueError):
            return None
    mapped = getattr(ip, "ipv4_mapped", None)
    ip = mapped or ip
    if ip in _BLOCKED_ADDRS or any(ip.version == n.version and ip in n for n in _BLOCKED_NETS):
        return f"address {host} is link-local, a metadata service or unspecified"
    return None


class HostManifest(BaseModel):
    """`hosts/<name>.yaml` (runtime root only)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=NAME_RE, max_length=64)
    host: str = Field(min_length=1, max_length=253, pattern=r"^[A-Za-z0-9:\[][A-Za-z0-9._:%\[\]-]*$")
    """Hostname or IP literal; never `user@host`, an option or a URL."""
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")
    key_secret: str = Field(pattern=r"^secret:[A-Za-z0-9_.-]+$")
    """`secret:<NAME>`: the private key in the secret store. Never a path or key text."""
    keepalive_interval_s: float = Field(default=30, ge=0, le=3600)
    """Seconds between SSH keepalives on an idle connection; 0 disables them."""

    @field_validator("name")
    @classmethod
    def _not_reserved(cls, v: str) -> str:
        return check_reserved(v)

    @field_validator("host")
    @classmethod
    def _safe_address(cls, v: str) -> str:
        if (why := unsafe_address(v)) is not None:
            raise ValueError(why)
        return v


def load_host(c: Candidate) -> Any:
    """Loader for the `hosts` kind. The loader helpers are imported here, not at module level,
    so `harness.loader` can import this module in either order."""
    from auoreo.harness import loader

    if c.root.source != "runtime":
        raise loader.LoadError(c.path, RUNTIME_ONLY)
    data, text = loader._parse_yaml(c)
    manifest = loader._validate(HostManifest, data, c.path, text)
    loader._check_name(c, manifest.name, text)
    return loader.Loaded(manifest, c.root.source, c.path)
