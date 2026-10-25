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
