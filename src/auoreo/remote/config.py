"""`settings.environment_remote` / `settings.bridges` / `settings.instance_name`.

Pure pydantic, no auoreo imports, so `core.settings` can nest these without a cycle.
Credentials are always `secret:<name>` references, resolved through the secret store.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


def secret_ref(v: str) -> str:
    if not v.startswith("secret:") or len(v) <= len("secret:"):
        raise ValueError("must be a 'secret:<name>' reference, never a value")
    return v


class RemoteEnvironment(BaseModel):
    """Client config for `backends/environment/remote` (an `auoreo exec-server`)."""

    model_config = ConfigDict(extra="forbid")
    url: str = Field(pattern=r"^wss?://")
    """`ws://127.0.0.1:7071` (loopback / SSH tunnel) or `wss://…` behind a TLS proxy."""
    token: str
    """`secret:<name>`: the exec-server's bearer token."""
    timeout_s: float = Field(default=30, gt=0, le=3600)

    @field_validator("token")
    @classmethod
    def _ref(cls, v: str) -> str:
        return secret_ref(v)


class BridgePeer(BaseModel):
    """A peer AUOREO instance reachable for `send_message(to="bridge:<name>/<member>")`."""

    model_config = ConfigDict(extra="forbid")
    url: str = Field(pattern=r"^(https?|unix)://")
    """Peer base URL (`https://peer:4096`) or `unix:///path/to/auoreo.sock`."""
    secret: str
    """`secret:<name>`: the shared HMAC key (both sides configure the same value)."""
    as_name: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,32}$")
    """The name this instance presents to that peer (the peer's `bridges` key for us);
    defaults to `settings.instance_name`."""

    @field_validator("secret")
    @classmethod
    def _ref(cls, v: str) -> str:
        return secret_ref(v)
