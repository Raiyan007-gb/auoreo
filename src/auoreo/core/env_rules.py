"""Name rules for environment-style variables (no `.env` file is ever loaded at startup).

- `is_protected(name)` — security knobs (`AUOREO_YOLO`, `AUOREO_PERMISSION_MODE`,
  `AUOREO_SANDBOX*`, `AUOREO_REDACT_*`, …): read ONLY from the real process environment, never
  from the database, the web or an imported `.env` (§19.3).
- `project_allowed(name)` — the allowlist a repository-controlled `<cwd>/.env` is held to when
  the web's one-time "Import existing .env" reads it (I1): of the `AUOREO_*` names only
  `PROJECT_AUOREO_ALLOW` pass, and names that would redirect traffic or code loading
  (`REDIRECT`: `*_API_BASE`, `*_URL`, `*_HOST`, proxies, CA bundles, `LD_*`, `PYTHON*`, `PATH`)
  are not imported. Provider credentials (`*_API_KEY`, `*_TOKEN`, …) are.
- `REDIRECT` also reserves those names for custom variables (`core.env_custom`).
"""

from __future__ import annotations

import re

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
    "AUOREO_HOME",  # would swap in a state.db (sandbox, redaction, rules) someone else controls
    "AUOREO_PROFILE",  # same: selects <home>/profiles/<name>/state.db
    "AUOREO_KANBAN_BOARD",  # I3: a kanban worker's hard board boundary
    "AUOREO_TEST_PROVIDER_BASE",  # tests only: repoints provider model-list calls (fake gateway)
)
PROJECT_AUOREO_ALLOW: frozenset[str] = frozenset(
    {
        "AUOREO_MODEL",
        "AUOREO_SMALL_MODEL",
        "AUOREO_EMBED_MODEL",
        "AUOREO_EMBED_DIMS",
        "AUOREO_AGENT",
        "AUOREO_WORKFLOW",
        "AUOREO_GATEWAY",
        "AUOREO_PORT",
    }
)
"""The only `AUOREO_*` names a project `.env` import may move into the database (I1)."""
REDIRECT = re.compile(
    r"(_API_BASE|_URL|_ENDPOINT|_PROXY|_CA_BUNDLE|_HOST)$|^(LD_|DYLD_|PYTHON|SSL_CERT|NODE_|GIT_)"
    r"|^(PATH|HOME|SHELL|TMPDIR|LITELLM_.*)$",
    re.IGNORECASE,
)
"""Non-`AUOREO_` names that would repoint provider traffic or code loading."""


def is_protected(name: str) -> bool:
    upper = name.upper()
    return any(upper.startswith(p) for p in PROTECTED_PREFIXES)


def project_allowed(name: str) -> bool:
    """Allowlist for a repository-controlled `<cwd>/.env` (import only)."""
    upper = name.upper()
    if upper.startswith("AUOREO_"):
        return upper in PROJECT_AUOREO_ALLOW
    return REDIRECT.search(upper) is None


__all__ = ["PROJECT_AUOREO_ALLOW", "PROTECTED_PREFIXES", "REDIRECT", "is_protected", "project_allowed"]
