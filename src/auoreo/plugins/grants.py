"""Plugin capability grants (`consumes:` in plugin.yaml)."""

from __future__ import annotations


def secret_granted(name: str, consumes: list[str]) -> bool:
    """`secrets:<NAME>` grants exactly NAME; `secrets:<PREFIX>*` grants names starting with a
    prefix of at least 3 characters (e.g. `MIIO_TOKEN_*`). Nothing broader (`secrets:*`)."""
    for grant in consumes:
        if not grant.startswith("secrets:"):
            continue
        pattern = grant[len("secrets:") :]
        if pattern == name:
            return True
        prefix = pattern[:-1]
        if pattern.endswith("*") and len(prefix) >= 3 and "*" not in prefix and name.startswith(prefix):
            return True
    return False
