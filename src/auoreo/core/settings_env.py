"""`settings.env`: user-defined custom variables managed from the web (Settings → Environment).

Plain values live here (settings.json); a secret variable keeps only its metadata here and its
value write-only in secrets.db under the same name. Nothing in this group is a security knob:
a variable is only ever *added* to a shell tool's child environment (on top of the scrubbed
allowlist) when `expose_to_shell` is set, and only listed by name to the model when
`expose_to_agent` is set (plain values too, never secret ones).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, model_validator

CUSTOM_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class CustomVar(BaseModel):
    secret: bool = False
    expose_to_shell: bool = False
    expose_to_agent: bool = False
    value: str | None = Field(default=None, max_length=8192)
    """Plain variables only; always None for a secret one (its value is in secrets.db)."""
    updated_at: str | None = None
    """ISO-8601 UTC timestamp of the last write."""

    @model_validator(mode="after")
    def _no_secret_value(self) -> CustomVar:
        if self.secret and self.value is not None:
            raise ValueError("a secret custom variable never stores its value in the settings store")
        return self


class EnvSettings(BaseModel):
    custom: dict[str, CustomVar] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _names(self) -> EnvSettings:
        bad = [n for n in self.custom if not CUSTOM_NAME.match(n)]
        if bad:
            raise ValueError(f"invalid custom variable name(s): {', '.join(sorted(bad))}")
        return self


__all__ = ["CUSTOM_NAME", "CustomVar", "EnvSettings"]
