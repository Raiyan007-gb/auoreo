"""`hosts/<name>.yaml`: one SSH host of the inventory (M7 hosts-a), and its loader.

A host file says which stored key is offered to which address, so it is accepted from the
runtime root (`<home>/hosts/`) only: never from a project `.auoreo/` (trusted or not), an
`extra_roots` folder or the bundled definitions. It holds no key material: `key_secret` is a
`secret:<NAME>` reference into the secret store; public keys and fingerprints are not stored
(the server's host key lives in `<home>/ssh/known_hosts`, written only by `auoreo ssh test`).
"""

from __future__ import annotations

import ipaddress
import logging
import os
import socket
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from auoreo.harness.manifests import NAME_RE, check_reserved

if TYPE_CHECKING:
    from auoreo.harness.discovery import Candidate, Root

log = logging.getLogger(__name__)

RUNTIME_ONLY = "hosts load from the runtime root only (<home>/hosts/); this file is ignored"

_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in ("169.254.0.0/16", "fe80::/10"))
_BLOCKED_ADDRS = frozenset(
    ipaddress.ip_address(a) for a in ("100.100.100.200", "0.0.0.0", "::", "fd00:ec2::254")
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_V4_COMPAT = ipaddress.ip_network("::/96")
"""IPv6 forms that carry an IPv4 address in their low 32 bits (6to4 uses `sixtofour`)."""


def _embedded_v4(ip: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 literal wraps: mapped, 6to4 (`2002::/16`), NAT64
    (`64:ff9b::/96`) or IPv4-compatible (`::a.b.c.d`)."""
    if (v4 := ip.ipv4_mapped or ip.sixtofour) is not None:
        return v4
    if ip in _NAT64 or ip in _V4_COMPAT:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip in _BLOCKED_ADDRS or any(ip.version == n.version and ip in n for n in _BLOCKED_NETS)


def unsafe_address(host: str) -> str | None:
    """Why `host` may not be an SSH target, or None. Only literals are checked: link-local and
    cloud-metadata addresses and the unspecified address. A hostname is resolved at connect
    time and not re-checked (known limitation, `docs/environments.md`). The numeric spellings a
    resolver reads as IPv4 without a lookup (`2852039166`, `0xa9fea9fe`, `169.254.43518`) count
    as literals, and so do the IPv6 forms that wrap an IPv4 address (mapped, 6to4, NAT64,
    IPv4-compatible) and the AWS IPv6 metadata address `fd00:ec2::254`."""
    text = host.strip().strip("[]").split("%", 1)[0]
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        try:
            ip = ipaddress.IPv4Address(socket.inet_aton(text))
        except (OSError, ValueError):
            return None
    inner = _embedded_v4(ip) if isinstance(ip, ipaddress.IPv6Address) else None
    if _blocked(ip) or (inner is not None and _blocked(inner)):
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


def hosts_stamp(roots: Iterable[Root]) -> tuple[tuple[str, int, int, int], ...]:
    """Cheap change marker of the runtime root's `hosts/*.yaml` (name, inode, mtime_ns, size):
    another process (`auoreo ssh add`, an editor, `rm`) edits these files under a running
    server, and `Harness.hosts` compares this on every read instead of watching."""
    out: list[tuple[str, int, int, int]] = []
    for root in roots:
        if root.source != "runtime":
            continue
        try:
            with os.scandir(root.path / "hosts") as it:
                for e in it:
                    if e.name.endswith(".yaml"):
                        st = e.stat()
                        out.append((e.name, st.st_ino, st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(sorted(out))


def load_runtime_hosts(roots: Iterable[Root]) -> dict[str, Any]:
    """The `hosts` kind re-read from the runtime root alone (an invalid file is skipped)."""
    from auoreo.harness import loader
    from auoreo.harness.discovery import iter_candidates

    out: dict[str, Any] = {}
    for cand in iter_candidates([r for r in roots if r.source == "runtime"], "hosts"):
        try:
            item = load_host(cand)
        except loader.LoadError as err:
            log.warning("skipping host file: %s", err)
            continue
        out.setdefault(cand.name, item)
    return out


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
