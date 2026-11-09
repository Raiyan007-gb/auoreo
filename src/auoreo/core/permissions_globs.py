"""Sensitive write targets (spec §19.7), split out of `permissions.py` (file-size cap)."""

from __future__ import annotations

# Sensitive write targets (§19.7): always `ask`, even under yolo / acceptEdits.
SENSITIVE_WRITE_GLOBS: tuple[str, ...] = (
    "~/.ssh/*",
    "~/.bashrc",
    "~/.bash_profile",
    "~/.bash_login",
    "~/.profile",
    "~/.zshrc",
    "~/.zprofile",
    "~/.zshenv",
    "~/.netrc",
    "~/.pgpass",
    "~/.npmrc",
    "~/.pypirc",
    "~/.aws/*",
    "~/.config/gh/*",
    "~/.gnupg/*",
    "/etc/*",
    "*/.git/hooks/*",
    "$AUOREO_HOME/settings.json",
    "$AUOREO_HOME/.env",
    "$AUOREO_HOME/trusted_workspaces.json",  # an agent must never trust a workspace itself
    "$AUOREO_HOME/.db.key",
    "$AUOREO_HOME/.secrets.key",
    "$AUOREO_HOME/hosts/*",  # M7: a host file chooses which stored key goes to which address
    "$AUOREO_HOME/ssh/*",  # M7: known_hosts (trusted server keys) is written by the CLI only
)
