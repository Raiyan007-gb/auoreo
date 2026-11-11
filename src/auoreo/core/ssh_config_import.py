"""`auoreo ssh import`: host entries from an OpenSSH client config (M7 hosts-c, D5).

A deliberately small, hand-written reader (no `~/.ssh/config` semantics beyond these keys):

- read: `Host` (one alias per entry; patterns with `*`, `?` or `!` are skipped), `HostName`,
  `User`, `Port`, `ProxyJump` (one hop, naming another imported alias);
- never imported: `IdentityFile` (keys stay in the secret store), `ProxyCommand` (the entry
  is skipped), `Include` and `Match` (not followed; reported), `known_hosts` lines, agents;
- names are mapped to the host-file pattern (`[a-z][a-z0-9_]*`): lowercase, other characters
  become `_`, a leading digit gets an `h_` prefix; a collision skips the later entry.

The result is a dry run until the operator passes `--write`; every entry still goes through
`HostManifest` validation (metadata and link-local addresses are refused there).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from auoreo.harness.manifests_hosts import unsafe_address

_PATTERN = re.compile(r"[*?!]")
_NAME = re.compile(r"[^a-z0-9_]")
KEYS = frozenset({"hostname", "user", "port", "proxyjump"})
REFUSED = frozenset({"proxycommand"})
NOTED = frozenset({"identityfile", "include", "match", "certificatefile", "localforward", "remoteforward"})


@dataclass
class Entry:
    alias: str
    name: str
    fields: dict[str, Any] = field(default_factory=dict)
    jump_alias: str | None = None
    skip: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class ImportPlan:
    hosts: list[Entry] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    """`(alias, why)`"""
    notes: list[str] = field(default_factory=list)


def host_name(alias: str) -> str:
    name = _NAME.sub("_", alias.lower()).strip("_") or "host"
    return f"h_{name}" if not name[0].isalpha() else name[:64]


def _split(line: str) -> tuple[str, str] | None:
    line = line.split("#", 1)[0].strip()
    if not line:
        return None
    m = re.match(r"^([A-Za-z]+)\s*(?:=\s*|\s+)(.*)$", line)
    if m is None:
        return None
    return m.group(1).lower(), m.group(2).strip().strip('"')


def _host(value: str, plan: ImportPlan, out: list[Entry]) -> Entry | None:
    words = value.split()
    if len(words) != 1 or _PATTERN.search(value):
        plan.skipped.append((value[:80], "pattern or several aliases (not imported)"))
        return None
    entry = Entry(words[0], host_name(words[0]))
    out.append(entry)
    return entry


def _option(cur: Entry, key: str, value: str) -> None:
    if key in REFUSED:
        cur.skip = f"{key} is not supported"
    elif key in NOTED:
        cur.notes.append(f"{key} ignored")
    elif key in KEYS and key not in cur.fields:  # first value wins, as in OpenSSH
        cur.fields[key] = value


def _entries(text: str, plan: ImportPlan) -> list[Entry]:
    out: list[Entry] = []
    cur: Entry | None = None
    for raw in text.splitlines():
        kv = _split(raw)
        if kv is None:
            continue
        key, value = kv
        if key == "host":
            cur = _host(value, plan, out)
            continue
        if key in ("match", "include"):
            plan.notes.append(f"`{key.title()}` is not followed")
            cur = None if key == "match" else cur
            continue
        if cur is not None:
            _option(cur, key, value)
    return out


def _resolve(e: Entry, by_alias: dict[str, Entry], default_user: str) -> None:
    f = e.fields
    host = f.get("hostname") or e.alias
    if (why := unsafe_address(host)) is not None:
        e.skip = why
        return
    try:
        port = int(f.get("port", 22))
    except ValueError:
        e.skip = "port is not a number"
        return
    e.fields = {"host": host, "port": port, "user": f.get("user") or default_user}
    jump = f.get("proxyjump")
    if jump and jump.lower() != "none":
        if "," in jump:
            e.skip = "multi-hop ProxyJump is not supported (one hop only)"
        elif (hop := by_alias.get(jump.split("@")[-1])) is None or "@" in jump or ":" in jump:
            e.skip = f"ProxyJump {jump!r} must name another Host entry of this file"
        else:
            e.jump_alias = hop.alias


def plan(text: str, default_user: str = "root") -> ImportPlan:
    """Parse `text` into host entries to write (no I/O). `default_user` stands in for a
    missing `User` (OpenSSH uses the local user name; the CLI passes it)."""
    out = ImportPlan()
    entries = _entries(text, out)
    by_alias = {e.alias: e for e in entries}
    seen: set[str] = set()
    for e in entries:
        if e.name in seen:
            e.skip = f"name {e.name} already used by another entry"
        seen.add(e.name)
        if e.skip is None:
            _resolve(e, by_alias, default_user)
    for e in entries:
        if e.skip is None and e.jump_alias is not None:
            hop = by_alias[e.jump_alias]
            if hop.skip is not None or hop.jump_alias is not None:
                e.skip = f"its jump host {hop.alias} is not importable as a one-hop bastion"
            else:
                e.fields["jump"] = hop.name
    for e in entries:
        if e.skip:
            out.skipped.append((e.alias, e.skip))
        else:
            out.hosts.append(e)
    return out
