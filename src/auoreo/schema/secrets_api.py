"""`/api/secrets` (I2, spec §25.14 L1067): write-through + rotation from the web Models page.

Values only ever travel client → server; every response carries NAMES only."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SecretWrite(BaseModel):
    value: str = Field(min_length=1, max_length=16_384)


class SecretRotate(BaseModel):
    value: str = Field(min_length=1, max_length=16_384)
    grace_s: int | None = Field(default=None, ge=0, le=30 * 86_400)
    """How long `<name>#prev` keeps resolving; None = `h1b.rotate_grace_s` (24 h)."""


class SecretWriteResult(BaseModel):
    name: str
    action: Literal["set", "rotate"]
    kept_previous: bool = False
    prev_expires_at: datetime | None = None
    reloaded: bool
    """The running gateway picked the new value up (Router rebuilt, no restart)."""


class SecretNames(BaseModel):
    names: list[str]
    """Stored secret names (rotation metadata excluded); never values."""


__all__ = ["SecretNames", "SecretRotate", "SecretWrite", "SecretWriteResult"]
