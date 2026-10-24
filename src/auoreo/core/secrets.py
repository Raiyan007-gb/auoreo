"""Secret resolution: process env → OS keyring → encrypted `secrets.db` (spec §7).

`secrets.db` is a JSON map encrypted with Fernet. The Fernet key lives in the OS keyring;
if no usable keyring exists and `AUOREO_KEYRING=allow-file`, the key falls back to a
0600 file next to the store (local/test use only).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)
SERVICE = "auoreo"
KEY_NAME = "secrets-key"
ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TERM", "USER", "SHELL", "TMPDIR")


class SecretStoreError(RuntimeError):
    pass


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

    def get(self, name: str) -> str | None:
        """env → keyring → secrets.db. Never raises for a missing value."""
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
        value = secrets.get(name) if secrets else os.environ.get(name)
        if value is not None:
            env[name] = value
    return env
