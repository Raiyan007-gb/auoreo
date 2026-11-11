"""SSH host inventory (M7 hosts-a): the files under `<home>` and environment resolution.

- `<home>/hosts/<name>.yaml`: host definitions (`HostManifest`), written by `auoreo ssh add`.
  No key material: `key_secret` names a secret; the key itself stays in the secret store.
- `<home>/ssh/known_hosts`: trusted server host keys, unhashed OpenSSH lines. Written only by
  `trust()`, which only `auoreo ssh test` calls (trust-on-first-use is a CLI decision). The
  runtime backend reads it and never bypasses it; a changed key is an error, never a prompt.
- `resolve_environment`: the one lookup behind `use_environment`, the environment pool and
  doctor. `settings.environments[name]` wins; a host file is used only when
  `settings.m7.ssh.hosts` is on, and becomes an `environment_ssh` override in memory.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from auoreo.core.db_migrate import migrate_lock
from auoreo.core.fsutil import write_private
from auoreo.core.settings_models import SshEnvironment
from auoreo.harness.manifests_hosts import HostManifest, parse_host, unsafe_address

HEADER = "# Written by `auoreo ssh add`. No key material here: key_secret names a stored secret.\n"
_KEY_TYPE = re.compile(r"^[A-Za-z0-9@._+-]{1,64}$")
_KEY_B64 = re.compile(r"^[A-Za-z0-9+/]{16,}={0,2}$")


class HostKeyChanged(ValueError):
    """`known_hosts` holds a different key for this host and port."""


def ssh_dir(home: Path) -> Path:
    return Path(home) / "ssh"


def hosts_dir(home: Path) -> Path:
    return Path(home) / "hosts"


def known_hosts_path(home: Path) -> Path:
    return ssh_dir(home) / "known_hosts"


def reject_unsafe_address(host: str) -> None:
    """Raise `ValueError` for a link-local, metadata or unspecified address literal."""
    if (why := unsafe_address(host)) is not None:
        raise ValueError(why)


# ------------------------------------------------------------------ host files
def _manifest(name: str, fields: Mapping[str, Any]) -> HostManifest:
    try:
        return HostManifest.model_validate({**fields, "name": name})
    except ValidationError as exc:
        err = exc.errors()[0]
        where = ".".join(str(p) for p in err["loc"]) or "host"
        raise ValueError(f"{where}: {err['msg']}") from None  # never echoes the input value


def write_host(home: Path, name: str, fields: Mapping[str, Any], *, force: bool = False) -> Path:
    """Validate and write `<home>/hosts/<name>.yaml` atomically. Refuses an existing file
    unless `force` (`FileExistsError`); invalid input raises `ValueError`. v2 keys are written
    only when set, with `schema: 2` (`HostManifest.to_file`)."""
    manifest = _manifest(name, {k: v for k, v in fields.items() if k != "schema"})
    target = hosts_dir(home) / f"{manifest.name}.yaml"
    if target.exists() and not force:
        raise FileExistsError(f"{target} exists (use --force to overwrite)")
    target.parent.mkdir(parents=True, exist_ok=True)
    write_private(target, HEADER + yaml.safe_dump(manifest.to_file(), sort_keys=False))
    return target


def delete_host(home: Path, name: str) -> bool:
    """Remove `<home>/hosts/<name>.yaml`; False when there is none. Secrets and
    `known_hosts` are untouched (`auoreo ssh rm --forget-key` drops the latter)."""
    _manifest(name, {"host": "h", "user": "u", "key_secret": "secret:X"})  # name check only
    try:
        (hosts_dir(home) / f"{name}.yaml").unlink()
    except FileNotFoundError:
        return False
    return True


def read_hosts(home: Path) -> tuple[dict[str, HostManifest], list[str]]:
    """`(hosts by name, problems)` from `<home>/hosts/*.yaml` (manifests only, no network).
    A loaded file's reader warnings (an ignored unknown key) are listed as problems too."""
    out: dict[str, HostManifest] = {}
    problems: list[str] = []
    base = hosts_dir(home)
    for path in sorted(base.glob("*.yaml")) if base.is_dir() else []:
        if path.name.startswith((".", "_")):
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("name") != path.stem:
                raise ValueError("name must match the file name")
            out[path.stem], notes = parse_host(data)
            problems.extend(f"{path}: {note}" for note in notes)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            problems.append(f"{path}: {str(exc).splitlines()[0][:200]}")
    return out, problems


# ------------------------------------------------------------------ v2 runtime data
@dataclass(frozen=True)
class JumpHop:
    """The bastion of a host: its own address, user, key and keepalive (`known_hosts` is the
    shared harness file). The target's key is never offered to it."""

    name: str
    host: str
    port: int
    user: str
    key_secret: str
    keepalive_interval_s: float


@dataclass(frozen=True)
class HostRuntime:
    """v2 data of a host file that travels beside the `SshEnvironment` override (which is
    `extra="forbid"` and must not gain keys): set on the backend as `host_ext`."""

    name: str
    tags: tuple[str, ...] = ()
    audit_commands: str = "hash"
    jump: JumpHop | None = None
    problem: str | None = None
    """Why this host may not be used (a broken jump chain, jump hosts switched off)."""


def resolve_chain(found: Mapping[str, HostManifest], name: str) -> HostManifest | None:
    """The jump host of `name`, or None. `ValueError` for a broken chain: the jump is
    missing, is the host itself, or has a jump of its own (one hop only, so no cycle)."""
    m = found[name]
    if m.jump is None:
        return None
    if m.jump == name:
        raise ValueError(f"host '{name}' names itself as its jump host")
    hop = found.get(m.jump)
    if hop is None:
        raise ValueError(f"host '{name}': jump host '{m.jump}' is not defined")
    if hop.jump is not None:
        raise ValueError(f"host '{name}': jump host '{m.jump}' has a jump of its own (one hop only)")
    return hop


def jump_enabled(settings: Any) -> bool:
    return bool(getattr(getattr(getattr(settings, "m7", None), "ssh", None), "jump", False))


def host_runtime(svc: Any, m: HostManifest) -> HostRuntime:
    found = {n: h.manifest for n, h in _host_files(svc).items()}
    found.setdefault(m.name, m)
    base = HostRuntime(m.name, m.tags, m.audit_commands)
    if m.jump is None:
        return base
    if not jump_enabled(svc.settings):
        why = f"host '{m.name}' needs jump host '{m.jump}' and m7.ssh.jump is off (no direct connection)"
        return replace(base, problem=why)
    try:
        hop = resolve_chain(found, m.name)
    except ValueError as exc:
        return replace(base, problem=str(exc))
    assert hop is not None
    jump = JumpHop(hop.name, hop.host, hop.port, hop.user, hop.key_secret, hop.keepalive_interval_s)
    return replace(base, jump=jump)


def manifest_to_override(m: HostManifest, home: Path) -> dict[str, Any]:
    """The Settings override a host file stands for (in memory only, never persisted)."""
    cfg = SshEnvironment(
        host=m.host,
        port=m.port,
        username=m.user,
        key_secret=m.key_secret,
        known_hosts=known_hosts_path(home),
        keepalive_interval_s=m.keepalive_interval_s,
    )
    return {"environment_ssh": cfg}


# ------------------------------------------------------------------ resolution
@dataclass(frozen=True)
class ResolvedEnv:
    name: str
    backend: str
    overrides: dict[str, Any] = field(default_factory=dict)
    origin: str = "settings"
    """`settings` (`settings.environments`) or `host-file` (`hosts/<name>.yaml`)."""
    description: str = ""
    host: HostRuntime | None = None
    """v2 data of a host file (tags, audit mode, jump hop); None for settings entries."""


def hosts_enabled(settings: Any) -> bool:
    return bool(getattr(getattr(getattr(settings, "m7", None), "ssh", None), "hosts", False))


def _host_files(svc: Any) -> dict[str, Any]:
    if not hosts_enabled(svc.settings):
        return {}
    return dict(getattr(getattr(svc, "harness", None), "hosts", None) or {})


def host_files(svc: Any) -> dict[str, Any]:
    """Loaded host files of `svc` (empty while `m7.ssh.hosts` is off)."""
    return _host_files(svc)


def resolve_environment(svc: Any, name: str) -> ResolvedEnv | None:
    """The environment `name` of `svc` (anything with `.settings` and `.harness`)."""
    entry = svc.settings.environments.get(name)
    if entry is not None:
        overrides = dict(getattr(entry, "model_extra", None) or {})
        return ResolvedEnv(name, entry.backend, overrides, "settings", entry.description)
    loaded = _host_files(svc).get(name)
    if loaded is None:
        return None
    m = loaded.manifest
    overrides = manifest_to_override(m, Path(svc.settings.home))
    return ResolvedEnv(
        name, "ssh", overrides, "host-file", m.description or "ssh host file", host_runtime(svc, m)
    )


def environment_names(svc: Any) -> list[str]:
    return sorted({*svc.settings.environments, *_host_files(svc)})


def shadowed_hosts(svc: Any) -> list[str]:
    """Host files hidden by a `settings.environments` entry of the same name."""
    return sorted(set(svc.settings.environments) & set(_host_files(svc)))


def unknown_message(svc: Any, name: str, *, none: str = "none") -> str:
    known = ", ".join(sorted(svc.settings.environments)) or none
    text = f"unknown environment '{name}' (settings.environments: {known}"
    if hosts_enabled(svc.settings):
        text += f"; hosts: {', '.join(sorted(_host_files(svc))) or 'none'}"
    return text + ")"


# ------------------------------------------------------------------ known_hosts
@dataclass(frozen=True)
class ProbeResult:
    key_type: str
    fingerprint_sha256: str
    openssh_line: str
    """`<key-type> <base64>`: the public host key the server presented."""


def _key_fields(line: str) -> tuple[str, str]:
    """`(key type, base64)` of a public-key line; anything after them (a comment) is dropped."""
    if "\n" in line or "\r" in line:
        raise ValueError("a host key is one line")
    parts = line.split()
    if len(parts) < 2 or not _KEY_TYPE.match(parts[0]) or not _KEY_B64.match(parts[1]):
        raise ValueError("not a public host key line (<key-type> <base64>)")
    return parts[0], parts[1]


def fingerprint_of(line: str) -> str:
    """`SHA256:<base64>` of a public-key line, as OpenSSH prints it."""
    _, b64 = _key_fields(line)
    digest = hashlib.sha256(base64.b64decode(b64 + "=" * (-len(b64) % 4))).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def host_pattern(host: str, port: int) -> str:
    bare = host.strip("[]")
    return bare if port == 22 else f"[{bare}]:{port}"


def _entries(text: str, pattern: str) -> list[tuple[int, list[str], str]]:
    """`(line index, host patterns, "<type> <b64>")` of the plain entries naming `pattern`."""
    out = []
    for i, raw in enumerate(text.splitlines()):
        parts = raw.split()
        if len(parts) < 3 or parts[0].startswith(("#", "@")):
            continue  # comments and @cert-authority/@revoked markers are never ours to touch
        names = parts[0].split(",")
        if pattern in names:
            out.append((i, names, f"{parts[1]} {parts[2]}"))
    return out


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def known_keys(home: Path, host: str, port: int) -> list[str]:
    """The `<type> <b64>` keys `<home>/ssh/known_hosts` trusts for this host and port."""
    return [key for _, _, key in _entries(_read(known_hosts_path(home)), host_pattern(host, port))]


def trust(home: Path, host: str, port: int, line: str, *, replace: bool = False) -> bool:
    """Record `line` (`<key-type> <base64>`) as the host key of `host:port`. Returns False when
    that key is already trusted (nothing is written). A different key already on file raises
    `HostKeyChanged` unless `replace`, which swaps the entry. Atomic, under a file lock."""
    key = " ".join(_key_fields(line))
    pattern = host_pattern(host, port)
    if not re.fullmatch(r"[A-Za-z0-9._:%\[\]-]+", pattern):
        raise ValueError(f"not a host name or address: {host!r}")
    path = known_hosts_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with migrate_lock(path):
        text = _read(path)
        found = _entries(text, pattern)
        if any(k == key for _, _, k in found) and (not replace or len(found) == 1):
            return False
        if found and not replace:
            raise HostKeyChanged(f"{pattern} already has a different host key in {path}")
        lines = text.splitlines()
        for i, names, old in found:
            rest = [n for n in names if n != pattern]
            lines[i] = f"{','.join(rest)} {old}" if rest else ""
        kept = [ln for i, ln in enumerate(lines) if ln or i not in {j for j, _, _ in found}]
        write_private(path, "\n".join([*kept, f"{pattern} {key}"]) + "\n")
    return True


async def probe_host_key(host: str, port: int, timeout: float = 10.0) -> ProbeResult:
    """The host key `host:port` presents, without authenticating (the connection ends after
    key exchange). CLI trust-on-first-use only: nothing at runtime calls this, and the result
    is trusted only after the operator confirms its fingerprint."""
    import asyncssh

    key = await asyncio.wait_for(asyncssh.get_server_host_key(host, port, config=None), timeout)
    if key is None:
        raise ValueError(f"{host}:{port} presented no host key")
    line = " ".join(key.export_public_key().decode().split()[:2])
    return ProbeResult(key.get_algorithm(), key.get_fingerprint(), line)
