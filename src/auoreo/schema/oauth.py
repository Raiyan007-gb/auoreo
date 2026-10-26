"""I7: OAuth connector settlement (`auoreo login <connector>` → `authorization/settled`)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OAuthSettle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flow: str = Field(default="", max_length=32, description="Flow that signed in (loopback/device/manual)")


class OAuthSettled(BaseModel):
    connector: str
    settled: bool = Field(description="A refresh token is stored as `oauth/<connector>`")
    flow: str = ""
