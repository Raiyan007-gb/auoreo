"""`/api/hosts` wire contract (M7 hosts-c, `m7.ssh.web`): the SSH host inventory as data.

No model has a field for a private key, a public key body or a secret value: a key is named
(`key_name`, a secret NAME) and described by its fingerprint and whether the store has it.
Key generation, deployment, rotation and host-key trust are CLI-only (`auoreo ssh ...`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

NAME = r"^[a-z][a-z0-9_]*$"
SECRET = r"^[A-Za-z0-9_.-]{1,64}$"


class HostInfo(BaseModel):
    name: str
    host: str
    port: int
    user: str
    key_name: str = Field(description="NAME of the stored secret holding the private key")
    key_present: bool = Field(description="The secret store has a value under key_name")
    key_fingerprint: str | None = Field(default=None, description="SHA256 fingerprint (public), if known")
    trusted: bool = Field(description="known_hosts has a host key for host:port (`auoreo ssh test`)")
    host_key_fingerprints: list[str] = Field(default_factory=list)
    jump: str | None = None
    tags: list[str] = Field(default_factory=list)
    audit_commands: Literal["hash", "redacted"] = "hash"
    keepalive_interval_s: float = 30
    description: str = ""
    shadowed: bool = Field(default=False, description="A settings.environments entry has the same name")
    usable_by_agents: bool = Field(description="m7.ssh.hosts is on, the host is trusted and not shadowed")
    problem: str | None = Field(default=None, description="Why agents cannot use it (a broken jump chain, …)")


class HostWrite(BaseModel):
    """The editable fields. A changed address or port is untrusted until `auoreo ssh test`."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    user: str = Field(min_length=1, max_length=64)
    key_name: str = Field(pattern=SECRET, description="A secret NAME, never key text")
    keepalive_interval_s: float = Field(default=30, ge=0, le=3600)
    jump: str | None = Field(default=None, pattern=NAME, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=8)
    audit_commands: Literal["hash", "redacted"] = "hash"
    description: str = Field(default="", max_length=200)


class HostCheck(BaseModel):
    ok: bool
    detail: str


class HostList(BaseModel):
    hosts: list[HostInfo]
    problems: list[str] = Field(default_factory=list, description="Invalid or partly ignored host files")
    enabled: bool = Field(description="settings.m7.ssh.hosts: agents may use these hosts")
