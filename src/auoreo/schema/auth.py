"""`/api/auth/*`: cookie login for the browser (EventSource cannot send headers)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=4096)


class AuthStatus(BaseModel):
    secured: bool
    """A server password is configured."""
    authenticated: bool
    """This request carried a valid bearer token or cookie (always true when unsecured)."""
    # --- H1b
    expires_at: int | None = None
    """Unix time the session token expires (the cookie slides on activity); None otherwise."""
    method: str | None = None
    """How this request authenticated: `password` (Bearer), `jwt` (session token), `open`."""


class CapabilityRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)


class CapabilityToken(BaseModel):
    """A `/ws` credential scoped to one session (`serve --ws-auth capability`); pass it as
    `?token=` or `Authorization: Bearer` on the handshake. Never logged."""

    token: str
    session_id: str
    expires_at: int
