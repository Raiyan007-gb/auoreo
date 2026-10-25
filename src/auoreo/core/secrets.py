"""Secret resolution: process env → OS keyring → encrypted `secrets.db` (spec §7).

`secrets.db` is a JSON map encrypted with Fernet. The Fernet key lives in the OS keyring;
if no usable keyring exists and `AUOREO_KEYRING=allow-file`, the key falls back to a
0600 file next to the store (local/test use only).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from collections.abc import Callable, Iterable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)
SERVICE = "auoreo"
KEY_NAME = "secrets-key"
ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TERM", "USER", "SHELL", "TMPDIR")


class SecretStoreError(RuntimeError):
    pass


class UnscopedSecretError(SecretStoreError):
    """A tool asked for a secret outside its declared `requires_env` scope (spec §25.11)."""


@dataclass(frozen=True)
class SecretScope:
    """The secret names the running tool may read, and who it is (for the audit row)."""

    by: str
    names: frozenset[str]


SECRET_SCOPE: ContextVar[SecretScope | None] = ContextVar("auoreo_secret_scope", default=None)
AuditHook = Callable[[str, str, bool], None]
"""`(name, by, allowed)` — called for every scoped/attributed read; never sees the value."""


@contextlib.contextmanager
def secret_scope(by: str, names: Iterable[str]) -> Iterator[SecretScope]:
    """Install a per-call scope (the executor wraps each tool call in one)."""
    scope = SecretScope(by, frozenset(names))
    token = SECRET_SCOPE.set(scope)
    try:
        yield scope
    finally:
        SECRET_SCOPE.reset(token)


def _keyring_get(name: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE, name)
    except Exception:
        return None


def _keyring_set(name: str, value: str) -> bool:
    try:
        import keyring

        keyring.set_password(SERVICE, name, value)
        return keyring.get_password(SERVICE, name) == value
    except Exception:
        return False


def _owner_only(path: Path) -> None:
    if os.name == "posix":
        path.chmod(0o600)


class SecretStore:
    def __init__(self, home: Path, keyring_policy: str = "allow-file") -> None:
        self.path = home / "secrets.db"
        self.key_file = home / ".secrets.key"
        self.policy = keyring_policy
        self.audit_hook: AuditHook | None = None
        """Set by the `secret_audit` extension starter (no DB import here)."""

    # ---------------------------------------------------------------- key mgmt
    def _key(self, create: bool) -> bytes | None:
        if (k := _keyring_get(KEY_NAME)) is not None:
            return k.encode()
        if self.policy != "forbid-file" and self.key_file.is_file():
            return self.key_file.read_bytes().strip()
        if not create:
            return None
        key = Fernet.generate_key()
        if _keyring_set(KEY_NAME, key.decode()):
            return key
        if self.policy != "allow-file":
            raise SecretStoreError("no usable OS keyring and AUOREO_KEYRING forbids a key file")
        log.warning("keyring unavailable; storing secrets key in %s (0600)", self.key_file)
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        self.key_file.write_bytes(key)
        _owner_only(self.key_file)
        return key

    def _load(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        key = self._key(create=False)
        if key is None:
            raise SecretStoreError("secrets.db exists but its key is missing")
        try:
            data = json.loads(Fernet(key).decrypt(self.path.read_bytes()))
        except InvalidToken as exc:
            raise SecretStoreError("secrets.db cannot be decrypted with the current key") from exc
        return {str(k): str(v) for k, v in data.items()}

    def _save(self, data: dict[str, str]) -> None:
        key = self._key(create=True)
        assert key is not None
        token = Fernet(key).encrypt(json.dumps(data).encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(token)
        _owner_only(tmp)
        tmp.replace(self.path)

    # ------------------------------------------------------------------- API
    def set(self, name: str, value: str) -> None:
        data = self._load()
        data[name] = value
        self._save(data)

    def delete(self, name: str) -> bool:
        data = self._load()
        if name not in data:
            return False
        del data[name]
        self._save(data)
        return True

    def names(self) -> list[str]:
        return sorted(self._load())

    def get(self, name: str, *, by: str | None = None) -> str | None:
        """env → keyring → secrets.db. Never raises for a missing value.

        Inside a tool call (`SECRET_SCOPE` set) a name outside the tool's scope raises
        `UnscopedSecretError`. Scoped reads and reads attributed with `by=` (plugins,
        backends) are audited by NAME only through `audit_hook`."""
        scope = SECRET_SCOPE.get()
        if scope is not None or by is not None:
            who = by or (scope.by if scope else "unknown")
            allowed = scope is None or name in scope.names
            self._audit(name, who, allowed)
            if not allowed:
                raise UnscopedSecretError(
                    f"'{who}' may not read secret '{name}' (not in its requires_env scope)"
                )
        return self._resolve(name)

    def _audit(self, name: str, by: str, allowed: bool) -> None:
        if self.audit_hook is None:
            return
        try:
            self.audit_hook(name, by, allowed)
        except Exception:  # auditing must never break the read path
            log.exception("secret audit hook failed")

    def _resolve(self, name: str) -> str | None:
        if (v := os.environ.get(name)) is not None:
            return v
        if (v := _keyring_get(name)) is not None:
            return v
        try:
            return self._load().get(name)
        except SecretStoreError as exc:
            log.warning("secret store unavailable: %s", exc)
            return None


def child_env(requires: list[str], secrets: SecretStore | None) -> dict[str, str]:
    """Scrubbed environment for subprocesses: allowlist + the tool's declared needs."""
    env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    for name in requires:
        value = secrets._resolve(name) if secrets else os.environ.get(name)
        if value is not None:
            env[name] = value
    return env


async def start(services: object) -> None:
    """Extension starter: audit every scoped secret read as `secret.read{name, by}`."""
    import asyncio

    store = getattr(services, "secrets", None)
    audit = getattr(services, "audit", None)
    if store is None or audit is None:
        return None
    pending: set[asyncio.Task[object]] = set()

    def hook(name: str, by: str, allowed: bool) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        from auoreo.tools.context import CURRENT_SESSION

        task = loop.create_task(
            audit.record(
                session_id=CURRENT_SESSION.get(),
                actor="tool" if not by.startswith(("plugin:", "backend:")) else by.split(":", 1)[0],
                action="secret.read",
                decision="allow" if allowed else "deny",
                reason="scope" if allowed else "unscoped",
                detail={"name": name, "by": by},
            )
        )
        pending.add(task)
        task.add_done_callback(pending.discard)

    store.audit_hook = hook
    return None
