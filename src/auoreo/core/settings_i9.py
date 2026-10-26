"""I9: per-tool egress (loopback CONNECT proxy, `net: direct` grants), the search/fetch stack,
the guardian timeout and the WebSocket server pinning (spec §25.10 L1017/L1021, §25.12 L1037,
§25.15 L1097).

Attached to `Settings` as `i9: I9Settings`; set in `settings.json`
(`{"i9": {"egress": {"deny_hosts": ["*.internal"]}}}`) or as a JSON env var
(`AUOREO_I9='{"search": {"order": ["brave", "exa"]}}'`). `AUOREO_SMART_TIMEOUT` (a plain env
var, see `.env.example`) overrides `guardian.timeout_s`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SEARCH_PROVIDERS = ("exa", "parallel", "brave", "tavily", "jina", "firecrawl", "searxng")


class EgressSettings(BaseModel):
    """The loopback egress proxy every `net: proxy` tool goes through (spec §25.10)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    """Start the proxy with the server. Off: `net: proxy` in-process clients keep the
    DNS-pinned direct client (same SSRF floor) and sandboxed children get no network."""
    port: int = Field(default=0, ge=0, le=65535)
    """Loopback port (0 = ephemeral, chosen at start)."""
    allow_hosts: list[str] = Field(default_factory=list)
    """Host globs (`*.github.com`, `api.exa.ai`). Non-empty = only these hosts are reachable."""
    deny_hosts: list[str] = Field(default_factory=list)
    """Host globs always refused (checked before `allow_hosts`)."""
    allow_private_hosts: list[str] = Field(default_factory=list)
    """`host` or `host:port` globs the SSRF floor lets through to private/loopback addresses
    (local MCP servers, a LAN SearXNG). Configured search endpoints are added automatically."""
    allowed_ports: list[int] = Field(default_factory=list)
    """Destination ports a tunnel may open (`[]` = any port; e.g. `[80, 443]` to harden)."""
    connect_timeout_s: float = Field(default=15.0, gt=0, le=120)
    idle_timeout_s: float = Field(default=300.0, gt=0, le=3600)
    log_connections: bool = True
    """Log one line per tunnel (`host:port → ip`, verdict); never the payload."""


class NetDirectSettings(BaseModel):
    """`net: direct` = no proxy: approval + justification, one-shot grant, never default."""

    model_config = ConfigDict(extra="forbid")

    justification_ttl_h: float = Field(default=24.0, gt=0, le=24 * 30)
    """How long the plaintext justification is kept (`<home>/egress/justifications.json`);
    only its hash is written to the audit row."""


class ExaSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["fast", "auto", "deep"] = "auto"
    max_age_hours: int | None = 24
    """`contents.maxAgeHours` (replaces `livecrawl`): None omits it, 0 always fetches fresh."""
    text_max_chars: int = Field(default=8000, ge=0, le=100_000)
    """`contents.text.maxCharacters` (0 = highlights only)."""


class ParallelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["advanced", "basic", "turbo", "fast"] = "basic"
    """`advanced|basic|turbo` (remapped from agentic/fast/one-shot); `fast` kept for compat."""
    keyword_fanout: Literal["heuristic", "aux"] = "heuristic"
    """How the 2–3 keyword `search_queries` are derived from the objective: deterministic
    keyword extraction, or one temperature-0 aux call (falls back to the heuristic)."""
    max_chars_per_result: int = Field(default=1500, ge=100, le=30_000)


class TavilySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_depth: Literal["basic", "advanced"] = "basic"
    chunks_per_source: int = Field(default=3, ge=1, le=3)
    topic: Literal["general", "news", "finance"] = "general"


class SearchSettings(BaseModel):
    """`web_search` provider chain + result cache (spec §25.15 L1097)."""

    model_config = ConfigDict(extra="forbid")

    order: list[str] = Field(default_factory=lambda: list(SEARCH_PROVIDERS))
    """Provider order; a provider without its key/URL is skipped. First one that answers wins."""
    cache_ttl_s: int = Field(default=86_400, ge=0)
    """Search result cache TTL (SQLite `<home>/search_cache.db`; 0 = off)."""
    news_cache_ttl_s: int = Field(default=3600, ge=0)
    brave_endpoint: Literal["web", "llm_context"] = "web"
    """`/res/v1/web/search` (default) or `/res/v1/llm/context` (RAG grounding)."""
    firecrawl_base_url: str = "https://api.firecrawl.dev"
    """Self-hosted Firecrawl (AGPL, run it isolated) sets its own URL here."""
    exa: ExaSettings = Field(default_factory=ExaSettings)
    parallel: ParallelSettings = Field(default_factory=ParallelSettings)
    tavily: TavilySettings = Field(default_factory=TavilySettings)


class FetchSettings(BaseModel):
    """`web_fetch` behaviour beyond the SSRF floor."""

    model_config = ConfigDict(extra="forbid")

    respect_robots: bool = True
    """Refuse URLs disallowed by robots.txt for the `auoreo` user agent (blocked = blocked)."""
    denial_ttl_s: int = Field(default=3600, ge=0)
    """A host that answered 401/403/429 is skipped for this long (persistent denial cache)."""
    reader_fallback: list[Literal["jina", "firecrawl"]] = Field(
        default_factory=lambda: ["jina", "firecrawl"]  # type: ignore[arg-type]
    )
    """Readers tried (when their key is set) for JS-only / anti-bot pages before the browser hint."""
    js_min_text_chars: int = Field(default=200, ge=0)
    """Below this much visible text an HTML page with scripts counts as JS-only."""


class GuardianSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_s: float = Field(default=15.0, gt=0, le=120)
    """Smart-mode guardian call budget; `AUOREO_SMART_TIMEOUT` overrides it."""


class WsServerSettings(BaseModel):
    """Uvicorn WebSocket implementation + protocol pings (spec §25.12 L1037)."""

    model_config = ConfigDict(extra="forbid")

    impl: Literal["websockets-sansio", "websockets", "wsproto"] = "websockets-sansio"
    loopback_ping: bool = False
    """Protocol pings on a loopback bind (off: the app-level `ping{tick}` is authoritative;
    a half-open SSH tunnel is then only noticed by the app ping)."""
    ping_interval_s: float = Field(default=20.0, gt=0)
    ping_timeout_s: float = Field(default=20.0, gt=0)


class I9Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    egress: EgressSettings = Field(default_factory=EgressSettings)
    net_direct: NetDirectSettings = Field(default_factory=NetDirectSettings)
    search: SearchSettings = Field(default_factory=SearchSettings)
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    guardian: GuardianSettings = Field(default_factory=GuardianSettings)
    ws: WsServerSettings = Field(default_factory=WsServerSettings)
    reverse_proxy_url: str | None = None
    """Public URL of a reverse proxy in front of this server (`deploy/nginx/auoreo.conf`);
    when set, `auoreo doctor` fetches `/api/events` through it and asserts no buffering /
    `no-cache` and a first SSE frame within 30 s."""


__all__ = [
    "SEARCH_PROVIDERS",
    "EgressSettings",
    "ExaSettings",
    "FetchSettings",
    "GuardianSettings",
    "I9Settings",
    "NetDirectSettings",
    "ParallelSettings",
    "SearchSettings",
    "TavilySettings",
    "WsServerSettings",
]
