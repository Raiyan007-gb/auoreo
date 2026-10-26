"""I6: plugin trust tiers + registry, MCP client (roots, list_changed, SSE, sampling), webhook
providers and creator limits (spec §15, §19.11, §24.8, §24.10, §25.7, §25.15, §26.14).

Attached to `Settings` as `i6: I6Settings`; set in `settings.json`
(`{"i6": {"plugins": {"taps": ["https://example.com/plugins.json"]}}}`) or as a JSON env var
(`AUOREO_I6='{"creator": {"vm_timeout_ms": 2000}}'`). Every default keeps the pre-I6 behaviour
except the trust gates, which only ever refuse more.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SHA256_RE = r"^[0-9a-f]{64}$"


class AllowedPackage(BaseModel):
    """One allow-listed `oreo-plugin-*` / `auoreo-plugin-*` distribution, pinned by SHA-256."""

    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1)
    sha256: str = Field(pattern=SHA256_RE)
    """SHA-256 of the wheel/sdist pip downloads (`--require-hashes`)."""
    url: str | None = None
    """Direct wheel location (`https://…` or `file://…`) instead of the package index; still
    hash-pinned (`<package> @ <url> --hash=sha256:…`)."""
    requirements: list[str] = Field(default_factory=list)
    """Extra hash-pinned requirement lines for its dependencies (`dep==1.0 --hash=sha256:…`)."""


class PluginTrustSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundled_signatures: Literal["enforce", "warn", "off"] = "warn"
    """Bundled plugins carry an ed25519 signature (`plugins/SIGNATURES.json`). `enforce` refuses
    to start a bundled plugin whose folder no longer matches a signature by a pinned key;
    `warn` starts it and reports it in doctor; `off` skips the check."""
    extra_pubkeys: list[str] = Field(default_factory=list)
    """Additional raw ed25519 public keys (base64) trusted for bundled/registry signatures."""
    allow_list: dict[str, AllowedPackage] = Field(default_factory=dict)
    """`package name → {version, sha256}`: the only distributions `plugin pip-add` installs."""
    taps: list[str] = Field(default_factory=list)
    """Plugin registry indexes (HTTP(S) JSON: `{"plugins": [{name, package, version, sha256}]}`).
    A tap entry with a sha256 counts as allow-listed (the tap is the user's choice)."""
    index_ttl_s: int = Field(default=3600, ge=0)
    """How long a fetched tap index is reused; a stale cache is used when the tap is offline."""
    pip_timeout_s: float = Field(default=600.0, gt=0)
    entry_point_groups: list[str] = Field(default_factory=lambda: ["auoreo.plugin", "oreo.plugin"])
    """`importlib.metadata` entry-point groups enumerated (listed, never imported)."""


class McpClientSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sampling_iterations: int = Field(default=3, ge=1, le=10)
    """Sampling requests allowed per MCP tool call; the last one runs with `toolChoice: none`."""
    listen_tools_changed: bool = True
    """Open a `subscriptions/listen` stream (2026-07-28 servers) for `tools/list_changed`."""
    refresh_debounce_s: float = Field(default=0.2, ge=0)
    """Coalesce bursts of `tools/list_changed` into one re-list."""


class WebhookI6Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    github_meta_url: str = "https://api.github.com/meta"
    github_meta_refresh_s: int = Field(default=86_400, ge=60)
    """`allowed_cidrs: [github]` preset: refreshed at most daily, cached on disk."""
    github_meta_stale_days: int = Field(default=7, ge=1)
    """Doctor warns when the cached GitHub hook ranges are older than this."""


class CreatorI6Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vm_timeout_ms: int = Field(default=5000, ge=100, le=600_000)
    """`vmTimeoutMs`: wall-clock bound on a creator tool's SYNCHRONOUS body (`def run`). An
    `async def run` escapes it once it awaits; only the tool timeout bounds that."""


class I6Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    plugins: PluginTrustSettings = Field(default_factory=PluginTrustSettings)
    mcp: McpClientSettings = Field(default_factory=McpClientSettings)
    webhooks: WebhookI6Settings = Field(default_factory=WebhookI6Settings)
    creator: CreatorI6Settings = Field(default_factory=CreatorI6Settings)


__all__ = [
    "AllowedPackage",
    "CreatorI6Settings",
    "I6Settings",
    "McpClientSettings",
    "PluginTrustSettings",
    "WebhookI6Settings",
]
