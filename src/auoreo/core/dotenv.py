"""Load `.env` files into the process environment for the CLI/server (never for tools).

Precedence (spec §7): real environment > `secrets.db` > `<cwd>/.env` > `<home>/.env`.
Blank values (`KEY=`) are skipped so the placeholders in `.env.example` never shadow a key
from the keyring or `secrets.db`. Every name a `.env` file set is remembered
(`from_dotenv`) so `SecretStore` resolves `secrets.db` before it: a project `.env` only
fills gaps, it never shadows a managed secret.

Security knobs are never taken from a `.env` file (§19.3): `AUOREO_YOLO`,
`AUOREO_PERMISSION_MODE`, `AUOREO_SANDBOX*`, `AUOREO_REDACT_*`, … (`PROTECTED_PREFIXES`)
must come from the real process environment; a `.env` entry for one is ignored with a
warning. Child processes still get a scrubbed environment (`core.secrets.child_env`), and
the executor floor still refuses tool reads of `.env`.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from dotenv import dotenv_values

log = logging.getLogger(__name__)

PROTECTED_PREFIXES: tuple[str, ...] = (
    "AUOREO_YOLO",
    "AUOREO_PERMISSION",  # AUOREO_PERMISSION_MODE
    "AUOREO_SANDBOX",  # AUOREO_SANDBOX_POLICY, AUOREO_SANDBOX_DOCKER_*
    "AUOREO_BACKENDS",  # could select the `none` sandbox
    "AUOREO_REDACT",  # AUOREO_REDACT_SECRETS, AUOREO_REDACT_PHONES
    "AUOREO_SECURITY",
    "AUOREO_APPROVAL",  # AUOREO_APPROVALS, AUOREO_APPROVAL_TIMEOUT_S
    "AUOREO_FLOORS",
    "AUOREO_WEBHOOKS",  # strict_secrets
    "AUOREO_HTTP",  # host check / trusted hosts
    "AUOREO_KEYRING",
    "AUOREO_ALLOW_PRIVATE_URLS",
    "AUOREO_FAKE_GUARDIAN",
    "AUOREO_SMART",
    "AUOREO_INSECURE",
    "AUOREO_BUNDLED_ROOT",
    "AUOREO_EXTRA_ROOTS",
)
_ORIGIN: dict[str, str] = {}
"""name → sha256(value) for every variable a `.env` file put into `os.environ`."""


def is_protected(name: str) -> bool:
    upper = name.upper()
    return any(upper.startswith(p) for p in PROTECTED_PREFIXES)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def from_dotenv(name: str) -> bool:
    """True while `os.environ[name]` still holds the value a `.env` file supplied."""
    value = os.environ.get(name)
    return value is not None and _ORIGIN.get(name) == _digest(value)


def load_env_files(cwd: Path | None = None) -> list[str]:
    """Merge `.env` files into `os.environ` without overriding; returns the names set."""
    loaded: list[str] = []
    candidates = [(cwd or Path.cwd()) / ".env"]
    for path in candidates:
        loaded += _merge(path)
    # AUOREO_HOME may itself come from the project .env, so resolve home afterwards.
    from auoreo.core.paths import home_dir

    home_env = home_dir() / ".env"
    if home_env not in candidates:
        loaded += _merge(home_env)
    return loaded


def _merge(path: Path) -> list[str]:
    if not path.is_file():
        return []
    out: list[str] = []
    ignored: list[str] = []
    for name, value in dotenv_values(path).items():
        if not name or value is None or value.strip() == "" or name in os.environ:
            continue
        if is_protected(name):
            ignored.append(name)
            continue
        os.environ[name] = value
        _ORIGIN[name] = _digest(value)
        out.append(name)
    if ignored:
        log.warning(
            "ignored security setting(s) %s in %s: set them in the real environment",
            ", ".join(sorted(ignored)),
            path,
        )
    return out
