"""SSH client keys of the host inventory (M7 hosts-b, D5): generation, `authorized_keys`
lines, the remote edit commands and `<home>/ssh/keys.json`.

- A private key is generated in process (ed25519, no passphrase) and goes only to the secret
  store: never to argv, stdout, a file or an audit row. Its public half is always derived
  from the stored private key, never read back from `keys.json` (editing that file cannot
  plant a key).
- The remote edit is one fixed command built with `shlex.quote` from a line that matches
  `LINE_RE` (`ssh-ed25519 <base64> auoreo:<secret>:<fp8>`); nothing model- or user-written
  reaches the remote shell. The remote side is assumed to have `bash` (the backend already
  runs `bash -lc`).
- `keys.json` holds public data for `ssh list` and the web: per secret `{alg, fingerprint,
  created_at, rotated_at, prev_fingerprint, prev_expires, deployed}`.
"""

from __future__ import annotations

import json
import re
import shlex
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auoreo.core.db_migrate import migrate_lock
from auoreo.core.fsutil import write_private
from auoreo.core.hosts import _key_fields, fingerprint_of, ssh_dir

ALG = "ssh-ed25519"
LINE_RE = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/=]+ auoreo:[A-Za-z0-9_.-]+:[A-Za-z0-9+/]{8}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
PROBE = "echo auoreo-ssh-ok"
KEYS_FILE = "keys.json"


@dataclass(frozen=True)
class KeyPair:
    private: str = field(repr=False)
    """OpenSSH private key text: secret store only."""
    public: str
    """`ssh-ed25519 <base64>` (no comment)."""
    fingerprint: str
    """`SHA256:<base64>`."""


def generate() -> KeyPair:
    import asyncssh

    key = asyncssh.generate_private_key(ALG)
    return _pair(key, key.export_private_key().decode())


def from_private(text: str | None, *, secret: str) -> KeyPair:
    """The key pair of a stored private key. `ValueError` names the secret, never the text."""
    import asyncssh

    if not text:
        raise ValueError(f"secret {secret} is not set")
    try:
        key = asyncssh.import_private_key(text)
    except (asyncssh.KeyImportError, ValueError):
        raise ValueError(f"secret {secret} is not a valid unencrypted private key") from None
    return _pair(key, text)


def _pair(key: Any, private: str) -> KeyPair:
    public = " ".join(key.export_public_key().decode().split()[:2])
    return KeyPair(private, public, fingerprint_of(public))


def default_secret(host: str) -> str:
    return f"SSH_KEY_{host.upper()}"


def check_secret(name: str) -> str:
    name = name.removeprefix("secret:")
    if not SECRET_RE.match(name):
        raise ValueError("a secret name is 1-64 of A-Z a-z 0-9 _ . -")
    return name


def authorized_line(kp: KeyPair, secret: str) -> str:
    """`ssh-ed25519 <b64> auoreo:<secret>:<fp8>`; `ValueError` unless it matches `LINE_RE`."""
    fp8 = kp.fingerprint.removeprefix("SHA256:")[:8]
    line = f"{kp.public} auoreo:{check_secret(secret)}:{fp8}"
    if not LINE_RE.match(line):
        raise ValueError("refusing a malformed authorized_keys line")
    return line


def install_command(line: str) -> str:
    """Append `line` to `~/.ssh/authorized_keys` once (idempotent), creating it 0600."""
    if not LINE_RE.match(line):
        raise ValueError("refusing a malformed authorized_keys line")
    q = shlex.quote(line)
    return (
        'umask 077; d="$HOME/.ssh"; f="$d/authorized_keys"; mkdir -p "$d" && touch "$f" && '
        f'{{ grep -qxF {q} "$f" || printf \'%s\\n\' {q} >> "$f"; }}'
    )


def remove_command(public: str) -> str:
    """Drop every `authorized_keys` line whose key is `public` (`<type> <b64>`)."""
    key = " ".join(_key_fields(public))
    q = shlex.quote(key)
    return (
        'f="$HOME/.ssh/authorized_keys"; [ -f "$f" ] || exit 0; t="$f.auoreo.$$"; '
        f'awk -v k={q} \'($1" "$2) != k\' "$f" > "$t" && cat "$t" > "$f"; rc=$?; rm -f "$t"; exit $rc'
    )


# ------------------------------------------------------------------ keys.json
def keys_path(home: Path) -> Path:
    return ssh_dir(home) / KEYS_FILE


def read_keys(home: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(keys_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def record_key(home: Path, secret: str, **fields: Any) -> dict[str, Any]:
    """Merge public `fields` into the `keys.json` entry of `secret` (atomic, locked).
    `deployed` (a list of host names) is extended, not replaced."""
    path = keys_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with migrate_lock(path):
        data = read_keys(home)
        entry = dict(data.get(secret) or {})
        deployed = list(entry.get("deployed") or [])
        for host in fields.pop("deployed", None) or []:
            if host not in deployed:
                deployed.append(host)
        entry.update({"alg": ALG, **fields, "deployed": deployed})
        data[secret] = entry
        write_private(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
    return entry


def now() -> int:
    return int(time.time())
