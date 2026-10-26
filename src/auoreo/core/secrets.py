"""Secret resolution: process env → OS keyring → encrypted `secrets.db` → `.env` (spec §7).

`secrets.db` is a JSON map encrypted with Fernet. The Fernet key lives in the OS keyring;
if no usable keyring exists and `AUOREO_KEYRING=allow-file`, the key falls back to a
0600 file next to the store (local/test use only). Both files are written `0600` from the
first byte + atomic rename and checked owner-only on read (`core.owner_files`, I1). The
plaintext payload is `{"schema_version": 1, "entries": {...}}` (a legacy flat map still
reads); a newer `schema_version` than this code knows refuses to load.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from collections.abc import Callable, Iterable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from auoreo.core.dotenv import from_dotenv
from auoreo.core.keyring_policy import backend_allowed
from auoreo.core.owner_files import read_owner_only, write_owner_only

try:
    import fcntl
except ImportError:  # Windows: single-writer only
    fcntl = None  # type: ignore[assignment]

log = logging.getLogger(__name__)
SERVICE = "auoreo"
KEY_NAME = "secrets-key"
ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "TZ")
"""Closed global allowlist (spec §24.9 §7): PATH/HOME/LANG/TZ + `AUOREO_TELEMETRY_*`. Anything
else a tool needs comes from its manifest `requires_env`; tools that need a terminal type set
`TERM` themselves (terminal_pty's `term` argument), so it is not inherited from the host."""
ENV_ALLOWED_PREFIXES = ("AUOREO_TELEMETRY_",)
ROTATION_SUFFIXES = ("#prev", "#rotated_at", "#prev_expires")
UPDATED_SUFFIX = "#updated_at"
"""`<name>#updated_at` (epoch seconds) is written by every set/rotate; hidden from `names()`."""
SECRETS_SCHEMA_VERSION = 1


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
        keyring_ok = backend_allowed(self.policy)  # --- H1b: forbid-file refuses file keyrings too
        if keyring_ok and (k := _keyring_get(KEY_NAME)) is not None:
            return k.encode()
        if self.policy != "forbid-file" and self.key_file.is_file():
            return read_owner_only(self.key_file).strip()
        if not create:
            return None
        key = Fernet.generate_key()
        if keyring_ok and _keyring_set(KEY_NAME, key.decode()):
            return key
        if self.policy != "allow-file":
            raise SecretStoreError("no usable OS keyring and AUOREO_KEYRING forbids a key file")
        log.warning("keyring unavailable; storing secrets key in %s (0600)", self.key_file)
        write_owner_only(self.key_file, key)
        return key

    def _load(self) -> dict[str, str]:
        if not self.path.is_file():
            return {}
        key = self._key(create=False)
        if key is None:
            raise SecretStoreError("secrets.db exists but its key is missing")
        try:
            data = json.loads(Fernet(key).decrypt(read_owner_only(self.path)))
        except InvalidToken as exc:
            raise SecretStoreError("secrets.db cannot be decrypted with the current key") from exc
        if isinstance(data.get("schema_version"), int) and isinstance(data.get("entries"), dict):
            if data["schema_version"] > SECRETS_SCHEMA_VERSION:
                raise SecretStoreError(
                    f"secrets.db schema_version {data['schema_version']} is newer than this "
                    f"AUOREO ({SECRETS_SCHEMA_VERSION}): upgrade before using it"
                )
            data = data["entries"]
        return {str(k): str(v) for k, v in data.items()}

    def _save(self, data: dict[str, str]) -> None:
        key = self._key(create=True)
        assert key is not None
        payload = {"schema_version": SECRETS_SCHEMA_VERSION, "entries": data}
        write_owner_only(self.path, Fernet(key).encrypt(json.dumps(payload).encode()))

    # ------------------------------------------------------------------- API
    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """Cross-process exclusive lock around read-modify-write (spec §7 `flock`)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path.with_name(self.path.name + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def set(self, name: str, value: str) -> None:
        with self._locked():
            data = self._load()
            data[name] = value
            data[name + UPDATED_SUFFIX] = str(int(time.time()))
            self._save(data)

    def delete(self, name: str) -> bool:
        with self._locked():
            data = self._load()
            if name not in data:
                return False
            del data[name]
            data.pop(name + UPDATED_SUFFIX, None)
            self._save(data)
            return True

    def names(self) -> list[str]:
        return sorted(n for n in self._load() if not n.endswith(UPDATED_SUFFIX))

    def updated_at(self) -> dict[str, int]:
        """`name → epoch seconds` of the last set/rotate (names written before this existed
        have no entry). Never touches a value."""
        out: dict[str, int] = {}
        for n, v in self._load().items():
            if n.endswith(UPDATED_SUFFIX) and v.strip().isdigit():
                out[n.removesuffix(UPDATED_SUFFIX)] = int(v)
        return out

    # --- H1b: rotation with a grace window (spec §25.14) + values for the literal scanner
    def rotate(self, name: str, value: str, grace_s: int, now: float | None = None) -> bool:
        """New value into `name`; the old one moves to `<name>#prev` with `#rotated_at` and
        `#prev_expires` (now + grace). Returns whether a previous value was kept."""
        t = int(time.time() if now is None else now)
        with self._locked():
            data = self._load()
            old = data.get(name)
            if old and grace_s > 0:
                data.update({f"{name}#prev": old, f"{name}#rotated_at": str(t)})
                data[f"{name}#prev_expires"] = str(t + grace_s)
            else:
                for suffix in ROTATION_SUFFIXES:
                    data.pop(f"{name}{suffix}", None)
            data[name] = value
            data[name + UPDATED_SUFFIX] = str(t)
            self._save(data)
        return bool(old and grace_s > 0)

    def candidates(self, name: str) -> list[str]:
        """`[current, previous]` — the previous value only inside its grace window."""
        out = [v for v in (self._resolve(name), self._resolve(f"{name}#prev")) if v]
        return list(dict.fromkeys(out))

    def purge_expired(self, now: float | None = None) -> list[str]:
        """Drop `#prev` values whose grace window has passed; returns the base names."""
        t = time.time() if now is None else now
        with self._locked():
            data = self._load()
            gone = [
                n.removesuffix("#prev_expires")
                for n, v in data.items()
                if n.endswith("#prev_expires") and v.strip().isdigit() and int(v) <= t
            ]
            for base in gone:
                for suffix in ROTATION_SUFFIXES:
                    data.pop(f"{base}{suffix}", None)
            if gone:
                self._save(data)
        return gone

    def values(self) -> list[str]:
        """Every stored secret value (rotation metadata excluded) — for in-process matching
        only (the literal-secret command floor); never logged or returned to a tool."""
        try:
            data = self._load()
        except SecretStoreError:
            return []
        return [
            v for n, v in data.items() if not n.endswith(("#rotated_at", "#prev_expires", UPDATED_SUFFIX))
        ]

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
        """Real env → keyring → secrets.db → values a `.env` file supplied (spec §7)."""
        env = os.environ.get(name)
        if env is not None and not from_dotenv(name):
            return env
        if backend_allowed(self.policy) and (v := _keyring_get(name)) is not None:
            return v
        try:
            data = self._load()
            if name.endswith("#prev") and _expired(data.get(f"{name.removesuffix('#prev')}#prev_expires")):
                return None  # past its rotation grace window
            if (v := data.get(name)) is not None:
                return v
        except SecretStoreError as exc:
            log.warning("secret store unavailable: %s", exc)
        return env


def _expired(stamp: str | None) -> bool:
    return bool(stamp and stamp.strip().isdigit() and int(stamp) <= time.time())


def child_env(requires: list[str], secrets: SecretStore | None) -> dict[str, str]:
    """Scrubbed environment for subprocesses: allowlist + the tool's declared needs."""
    env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    env.update({k: v for k, v in os.environ.items() if k.startswith(ENV_ALLOWED_PREFIXES)})
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
