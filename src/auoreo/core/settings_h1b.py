"""H1b: browser session tokens, secret rotation/backup, keyring policy on public binds, plugin
quarantine and literal-secret command checks (spec §18, §24.7, §25.10, §25.14, §25.15).

Attached to `Settings` as `h1b: H1bSettings`; set in `settings.json`
(`{"h1b": {"jwt_ttl_s": 600}}`) or as a JSON env var (`AUOREO_H1B='{"jwt_bind_ip": false}'`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class H1bSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    jwt_ttl_s: int = Field(default=900, ge=60, le=900)
    """Lifetime of the browser session JWT (`POST /api/auth/login`); spec caps it at 15 min."""
    jwt_refresh_after_s: int = Field(default=120, ge=0)
    """A cookie-authenticated request older than this gets a fresh token (sliding session)."""
    jwt_bind_ip: bool = True
    """Bind the token to the client IP it was minted for (a replay from elsewhere is refused)."""
    capability_ttl_s: int = Field(default=900, ge=30, le=3600)
    """Lifetime of a per-session `/ws` capability token (`serve --ws-auth capability`)."""
    keyring_allow_file_public: bool = False
    """Explicit override: keep file-based keyrings even on a non-loopback / `--mdns` bind
    (default: those binds force `forbid-file`)."""
    rotate_grace_s: int = Field(default=86_400, ge=0)
    """Default `secrets rotate --grace`: how long `<name>#prev` still resolves after rotation."""
    literal_secret_min_len: int = Field(default=12, ge=6, le=256)
    """Shortest stored secret value the command scanner matches literally (shorter ones are
    too likely to collide with ordinary text)."""
