"""Keyring tiers on exposed binds (spec §7, §25.14).

`AUOREO_KEYRING=require|allow-file|forbid-file`: `allow-file` (local default) lets the Fernet
key fall back to a 0600 file; a non-loopback bind or `--mdns` forces `forbid-file` unless the
operator set `AUOREO_KEYRING` / `settings.keyring` explicitly or `h1b.keyring_allow_file_public`.
Under `forbid-file` plaintext *file* keyring backends (`keyrings.alt` PlaintextKeyring & co.)
are refused too — they are the same file on disk with a different name.

`probe()` feeds the doctor row `{kind, viable, headless}`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)
PLAINTEXT_BACKENDS = frozenset({"PlaintextKeyring", "EncryptedKeyring", "Keyring"})
"""`keyrings.alt.file.*` class names: files under the user's data dir, not an OS vault."""
FILE_MODULES = ("keyrings.alt.file", "keyrings.alt.Gnome", "keyrings.cryptfile")


class KeyringPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class KeyringProbe:
    kind: str
    viable: bool
    headless: bool
    file_backed: bool


def _backend() -> Any:
    try:
        import keyring

        return keyring.get_keyring()
    except Exception:
        return None


def is_file_backend(backend: Any) -> bool:
    if backend is None:
        return False
    cls = type(backend)
    mod = cls.__module__
    if mod.startswith(FILE_MODULES):
        return True
    return mod.startswith("keyrings.alt") and cls.__name__ in PLAINTEXT_BACKENDS


def probe() -> KeyringProbe:
    backend = _backend()
    kind = f"{type(backend).__module__}.{type(backend).__name__}" if backend is not None else "none"
    viable = backend is not None and float(getattr(backend, "priority", 0) or 0) > 0
    headless = os.name == "posix" and not (
        os.environ.get("DISPLAY") or os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    )
    return KeyringProbe(
        kind, viable and not kind.startswith("keyring.backends.fail"), headless, is_file_backend(backend)
    )


def backend_allowed(policy: str) -> bool:
    """False when `policy` forbids files and the active keyring is itself a plaintext file."""
    return policy != "forbid-file" or not is_file_backend(_backend())


def explicit(settings: Any) -> bool:
    return "keyring" in getattr(settings, "model_fields_set", set())


def enforce_public_bind(settings: Any, *, mdns: bool = False) -> str:
    """Force `forbid-file` on a non-loopback / mDNS bind (unless explicitly overridden) and
    refuse a plaintext file keyring there. Returns the effective policy."""
    exposed = mdns or not settings.is_loopback
    if not exposed:
        return str(settings.keyring)
    if explicit(settings) or settings.h1b.keyring_allow_file_public:
        if settings.keyring != "forbid-file":
            log.warning("keyring=%s kept on an exposed bind (explicit override)", settings.keyring)
        return str(settings.keyring)
    settings.keyring = "forbid-file"
    if not backend_allowed("forbid-file"):
        raise KeyringPolicyError(
            f"the active keyring backend ({probe().kind}) stores secrets in a plaintext file; an "
            "exposed bind needs an OS keyring (Secret Service / Keychain / Credential Locker) or an "
            "explicit AUOREO_KEYRING=allow-file"
        )
    return "forbid-file"


def doctor_row(settings: Any) -> tuple[str, str, str, str]:
    """`(check, level, detail, remedy)` — never shows a key."""
    p = probe()
    exposed = not settings.is_loopback
    policy = settings.keyring
    if exposed and not explicit(settings) and not settings.h1b.keyring_allow_file_public:
        policy = "forbid-file"
    detail = f"kind={p.kind} viable={p.viable} headless={p.headless} policy={policy}"
    if policy == "forbid-file" and p.file_backed:
        return (
            "keyring",
            "error",
            detail + " (plaintext file keyring refused)",
            "install an OS keyring backend",
        )
    if not p.viable and policy == "forbid-file":
        return (
            "keyring",
            "warn" if not exposed else "error",
            detail + " (no OS keyring; the secrets key cannot be stored)",
            "install/unlock an OS keyring, or set AUOREO_KEYRING=allow-file explicitly (local only)",
        )
    if not p.viable:
        return (
            "keyring",
            "warn",
            detail + " (fallback: 0600 key file)",
            "fine for local use; not on exposed binds",
        )
    return ("keyring", "ok", detail, "")
