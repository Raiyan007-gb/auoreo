"""Nested settings models (kept apart from `core.settings` for the 500-line cap).
Import them from `auoreo.core.settings`, which re-exports every name here."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelGroup(BaseModel):
    """One routing group → ordered deployments (LiteLLM `model_list` entries)."""

    model_config = ConfigDict(extra="forbid")
    models: list[str] = Field(default_factory=list)
    api_base: str | None = None
    max_input_tokens: int | None = None


class MemorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    embed_model: str = "openrouter/openai/text-embedding-3-small"
    dims: int = 1536
    rrf_k: int = 60
    memory_char_limit: int = 2200
    user_char_limit: int = 1375
    min_trust: float = 0.3
    default_trust: float = 0.5
    auto_inject_tokens: int = 2000
    tool_default_tokens: int = 800
    tool_max_tokens: int = 2000
    chunk_tokens: int = 512
    chunk_overlap: int = 50
    # --- M5f
    curator_enabled: bool = True
    curator_interval_hours: float = Field(default=168, gt=0)
    curator_min_idle_hours: float = Field(default=2, ge=0)
    curator_stale_after_days: float = Field(default=30, gt=0)
    curator_archive_after_days: float = Field(default=90, gt=0)
    # --- W1-D (spec §25.8): opt-in retrieval options, all off by default
    graph_enabled: bool = False
    """GraphRAG-lite: entity fan-out in search + community summaries for global queries."""
    graph_levels: int = Field(default=2, ge=1, le=2)
    graph_min_weight: int = Field(default=2, ge=1)
    graph_fanout_limit: int = Field(default=12, ge=1)
    graph_fanout_weight: float = Field(default=0.8, gt=0)
    graph_summary_tokens: int = Field(default=600, gt=0)
    recall_hyde: bool = False
    """HyDE: on zero FTS hits, embed query + a hypothetical answer for the vector arm only."""
    hyde_max_tokens: int = Field(default=128, gt=0)
    chunk_strategy: Literal["early", "late"] = "early"
    """`late` = contextual chunk embedding (a late-chunking approximation; memory/contextual.py)."""
    late_max_chars: int = Field(default=12_000, gt=0)


class LoopSettings(BaseModel):
    """§8.10 knobs. Invalid values warn and fall back (never raise)."""

    model_config = ConfigDict(extra="ignore")
    max_iterations: int = 500
    max_parallel_tool_calls: int = 10
    outer_loop_errors: int = 8
    doom_loop_repeats: int = 3
    tool_result_inline_chars: int = 64_000
    compaction_threshold_ratio: float = 0.80
    """Only used when set explicitly in settings.json: auto-compact at ratio × window."""
    compaction_retain_ratio: float = 0.16
    compaction_enabled: bool = True


# --- W2-A: execution environments (backends/environment/*)
class SshEnvironment(BaseModel):
    """`settings.environment_ssh`: the host the `ssh` environment backend connects to."""

    model_config = ConfigDict(extra="forbid")
    host: str
    port: int = Field(default=22, ge=1, le=65535)
    username: str
    key_secret: str | None = Field(default=None, pattern=r"^secret:[A-Za-z0-9_.-]+$")
    """`secret:<NAME>`: the private key (OpenSSH/PEM text) in the secret store, never a file."""
    known_hosts: Path | None = None
    """Required to connect: there is no host-key bypass (a mismatch fails closed)."""
    connect_timeout_s: float = Field(default=10.0, gt=0, le=120)


class NamedEnvironment(BaseModel):
    """`settings.environments.<name>`: a backend plus Settings overrides for it (e.g.
    `{"backend": "ssh", "environment_ssh": {...}}`), selected per session by `use_environment`."""

    model_config = ConfigDict(extra="allow")
    backend: str
    description: str = ""


IVR_ACTIONS = frozenset({"transfer", "say", "hangup", "followup"})


# --- W2-D: LiveKit SFU transport (P2, opt-in; backends/rtc_transport/livekit)
class LiveKitSipSettings(BaseModel):
    """`settings.livekit.sip`: SIP trunks are created on demand by the livekit backend helpers;
    these are the defaults. No REGISTER, no video; media is always SRTP `REQUIRE`."""

    model_config = ConfigDict(extra="forbid")
    inbound_numbers: list[str] = Field(default_factory=list)
    allowed_addresses: list[str] = Field(default_factory=list)
    """Carrier source IPs/CIDRs; `inbound_numbers=[]` needs these or digest auth."""
    auth_username: str | None = None
    auth_password_secret: str | None = None
    """Secret-store name of the inbound digest password (never the value)."""
    krisp_enabled: bool = False
    """F5: Krisp noise cancellation on SIP calls (`SIPInboundTrunkInfo` / `CreateSIPParticipant`).
    Off by default: on a self-hosted server it needs a Krisp-enabled build."""
    room_prefix: str = Field(default="auoreo-call-", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    outbound_address: str | None = None
    """Carrier SIP host for the outbound trunk (TLS transport)."""
    # --- F5
    ivr: dict[str, str] = Field(default_factory=dict)
    """DTMF digit → action: `transfer:<number>`, `say:<text>`, `hangup`, `followup:<text>`."""
    outbound_trunk_id: str | None = None
    """Outbound trunk used to dial the warm-transfer target (`CreateSIPParticipant`)."""

    @field_validator("ivr")
    @classmethod
    def _ivr(cls, v: dict[str, str]) -> dict[str, str]:
        for digit, action in v.items():
            if digit not in "0123456789*#ABCD" or len(digit) != 1:
                raise ValueError(f"ivr key {digit!r} is not a DTMF digit (0-9 * # A-D)")
            kind = action.split(":", 1)[0]
            if kind not in IVR_ACTIONS or (kind != "hangup" and not action.partition(":")[2].strip()):
                raise ValueError(f"ivr[{digit!r}] = {action!r}: use transfer:/say:/followup:<arg> or hangup")
        return v


class LiveKitSettings(BaseModel):
    """`settings.livekit`. Self-hosted by default; a `*.livekit.cloud` URL is refused unless
    `cloud_consent` is true (media then leaves your network)."""

    model_config = ConfigDict(extra="forbid")
    url: str | None = None
    """`wss://` / `https://` of the LiveKit server (clients connect to the same URL)."""
    api_key_secret: str = "LIVEKIT_API_KEY"
    api_secret_secret: str = "LIVEKIT_API_SECRET"
    cloud_consent: bool = False
    token_ttl_s: int = Field(default=3600, ge=60, le=86_400)
    sip: LiveKitSipSettings = Field(default_factory=LiveKitSipSettings)
    # --- F5
    region: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    """Region pin: `Destination.region` on outbound SIP calls (where the server supports it)."""


# --- end W2-D
