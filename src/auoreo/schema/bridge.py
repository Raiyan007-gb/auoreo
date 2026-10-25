"""Wire models for the remote plane (W2-B): the instance-bridge envelope and the
multi-environment listing (`GET /api/environments`)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NAME = r"^[A-Za-z0-9_.-]{1,32}$"
MEMBER = r"^[A-Za-z0-9_.*-]{1,64}$"


class BridgeEnvelope(BaseModel):
    """`POST /api/bridge/inbox` body. Signed with HMAC-SHA256 over
    `"<timestamp>.<nonce>." + raw body` (header `X-Auoreo-Signature: sha256=<hex>`)."""

    model_config = ConfigDict(extra="forbid")
    sender: str = Field(pattern=NAME, description="Sending instance, as the receiver's `bridges` key")
    from_member: str = Field(pattern=MEMBER)
    to_member: str = Field(pattern=MEMBER)
    team: str | None = Field(default=None, max_length=128, description="Receiver team id or name")
    message: str = Field(min_length=1, max_length=100_000)
    summary: str = Field(default="", max_length=200)
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{16,64}$")
    timestamp: int = Field(description="Unix seconds; accepted within ±300 s")


class BridgeDelivered(BaseModel):
    team: str
    recipient: str
    sender: str = Field(description="As stored in the receiver's mailbox: `bridge:<instance>/<member>`")
    mail_id: int


class EnvironmentInfo(BaseModel):
    """One place commands (or mail) can go. Never carries a secret."""

    name: str
    kind: Literal["backend", "named", "remote", "bridge"]
    backend: str | None = None
    remote: bool = False
    selected: bool = False
    ok: bool | None = Field(default=None, description="check() result; null when not checked")
    detail: str = ""
    url: str | None = Field(default=None, description="Endpoint without credentials, query or fragment")
