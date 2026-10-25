"""Non-secret configuration.

Precedence (first wins): process env `AUOREO_*` > `<home>/settings.json` > defaults.
Secrets never live here — see `core.secrets`.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from auoreo.core.paths import home_dir
from auoreo.core.settings_gateway import AuxSettings, GatewayTuning
from auoreo.core.settings_models import (
    LiveKitSettings,
    LiveKitSipSettings,
    LoopSettings,
    MemorySettings,
    ModelGroup,
    NamedEnvironment,
    SshEnvironment,
    VoiceHostBackends,
)
from auoreo.policy.config import BanditSettings
from auoreo.remote.config import BridgePeer, RemoteEnvironment  # --- W2-B

log = logging.getLogger(__name__)

PermissionMode = Literal["manual", "smart", "yolo", "plan", "acceptEdits"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUOREO_", extra="ignore", env_ignore_empty=True)

    home: Path = Field(default_factory=home_dir)
    host: str = "127.0.0.1"
    port: int = 4096
    server_password: str | None = None
    gateway: Literal["litellm", "fake"] = "litellm"
    model: str | None = None
    """`AUOREO_MODEL`: a routing group (`default`, `small`) or `<provider>/<model id>` in
    LiteLLM form (`openrouter/anthropic/claude-sonnet-4.5`, `anthropic/claude-sonnet-5`).
    A provider id also becomes the `default` group unless settings.json sets `models`."""
    small_model: str | None = None
    """`AUOREO_SMALL_MODEL`: `<provider>/<model id>` for aux calls (titles, compaction,
    guardian). Unset with a provider `AUOREO_MODEL` → the main model is reused."""
    embed_model: str | None = None
    """`AUOREO_EMBED_MODEL`: overrides `memory.embed_model` (pair with `AUOREO_EMBED_DIMS`)."""
    embed_dims: int | None = None
    agent: str = "default"
    workflow: str = "chat"
    permission_mode: PermissionMode = "manual"
    approval_timeout_s: float = 300.0
    strict_overlays: bool = False
    redact_secrets: bool = True
    keyring: Literal["require", "allow-file", "forbid-file"] = "allow-file"

    # settings.json-only structured config
    models: dict[str, ModelGroup] = Field(
        default_factory=lambda: {
            "default": ModelGroup(models=["openrouter/anthropic/claude-sonnet-4.5"]),
            "small": ModelGroup(models=["openrouter/openai/gpt-4o-mini"]),
        }
    )
    aliases: dict[str, str] = Field(default_factory=dict)
    fallbacks: dict[str, list[str]] = Field(default_factory=lambda: {"default": ["small"]})
    auxiliary: AuxSettings = Field(default_factory=AuxSettings)
    gateway_tuning: GatewayTuning = Field(default_factory=GatewayTuning)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    loop: LoopSettings = Field(default_factory=LoopSettings)
    extra_roots: list[Path] = Field(default_factory=list)
    """Additional definition roots (lowest precedence after bundled), e.g. `.claude`."""
    max_cost_usd: float | None = None
    """Per-session spend cap; a turn that crosses it finishes as `blocked` (code BUDGET)."""

    cors_origins: list[str] = Field(default_factory=list)
    """Extra browser origins allowed for CORS and the `/ws` Origin check (loopback always is)."""
    ws_ping_interval_s: float = 20.0
    """App-level WebSocket ping cadence; no pong for two intervals closes with 1011."""
    backends: dict[str, str] = Field(default_factory=dict)
    """Backend selection per kind, e.g. {"capture": "mss", "browser": "playwright"}; unset kinds
    fall back to the manifest marked `default: true`, then to the only candidate."""
    # --- M4e
    rtc_max_peers: int = 8
    """Concurrent WebRTC peers (`POST /api/rtc/offer`); one more answers 429."""
    rtc_ice_servers: list[str] = Field(default_factory=lambda: ["stun:stun.l.google.com:19302"])
    """STUN/TURN URLs for the server peer; `[]` = host candidates only (loopback/LAN)."""
    rtc_audio_level_interval_s: float = 1.0
    """Window (and minimum spacing) of `rtc.audio_level` events per inbound audio track."""
    rtc_speaking_rms: float = 0.02
    """Normalised RMS (0..1) above which a window counts as `speaking`."""

    # --- M4b
    allow_private_urls: bool = False
    """Let browser tools navigate to private/loopback hosts (SSRF guard off; tests, intranets)."""
    browser_executable_path: Path | None = None
    """Chromium binary for the playwright browser backend; overrides PLAYWRIGHT_BROWSERS_PATH."""

    @model_validator(mode="after")
    def _models_follow_env(self) -> Settings:
        """Provider ids in AUOREO_MODEL / AUOREO_SMALL_MODEL define the routing groups unless
        `models` was set explicitly (settings.json or init), so one env var is enough."""
        main = self.model if self.model and "/" in self.model else None
        small = self.small_model if self.small_model and "/" in self.small_model else None
        if "models" not in self.model_fields_set and (main or small):
            groups = dict(self.models)
            if main:
                groups["default"] = ModelGroup(models=[main])
            groups["small"] = ModelGroup(models=[small or main or groups["small"].models[0]])
            self.models = groups
        if self.embed_model or self.embed_dims:
            update = {"embed_model": self.embed_model, "dims": self.embed_dims}
            mem = self.memory.model_copy(update={k: v for k, v in update.items() if v})
            self.memory = mem
        return self

    @property
    def db_path(self) -> Path:
        return self.home / "state.db"

    @property
    def is_loopback(self) -> bool:
        return self.host in {"127.0.0.1", "localhost", "::1"}

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, _JsonFileSource(settings_cls))

    # --- M5 (shared contracts)
    hooks_enabled: bool = True
    scheduler_enabled: bool = True
    webhooks_enabled: bool = True
    teams_max_depth: int = Field(default=2, ge=1, le=4)
    teams_max_children: int = Field(default=3, ge=1, le=16)
    teams_child_timeout_s: int = Field(default=900, ge=10)
    plugins_enabled: bool = True

    # --- M5a
    automation_workers: int = Field(default=1, ge=1, le=16)
    """Concurrent delivery workers (each runs one agent + delivery at a time)."""
    automation_start_delay_s: float = Field(default=2.0, ge=0)
    """Grace before the outbox worker and scheduler catch-up start, so short CLI commands
    that build Services never claim rows or fire jobs."""
    automation_poll_s: float = Field(default=2.0, gt=0)
    """Outbox poll interval (rows enqueued by another process, e.g. `auoreo webhook test`)."""
    automation_run_timeout_s: int = Field(default=900, ge=10)
    """Agent timeout for webhook deliveries (jobs use their own `timeout_s`)."""
    # --- M5b
    searxng_url: str | None = None
    """Base URL of a SearXNG instance for `web_search` (JSON format enabled), e.g. `http://localhost:8888`."""
    # --- M5e
    plugins: dict[str, dict[str, Any]] = Field(default_factory=dict)
    """Per-plugin config (`plugins.<name>`), validated against the plugin's `config_schema`."""
    plugins_python: str = "3.12"
    """Interpreter request for plugin venvs (`uv venv --python <this>`)."""
    plugins_seed: bool = False
    """Pass `--seed` to `uv venv` (pip inside the plugin venv; needs the package index)."""
    plugins_poll_s: float = Field(default=30.0, gt=0)
    """Fallback poll of the harness snapshot for plugin hot reload."""
    plugins_tool_timeout_s: float = Field(default=60.0, gt=0)
    """Default plugin tool-call / delivery timeout (a tool entry's `timeout_ms` wins)."""
    plugins_backoff_scale: float = Field(default=1.0, ge=0)
    """Multiplier on the 1 s → 30 s crash-restart backoff (tests use 0)."""
    # --- M5h
    mcp_server_enabled: bool = True
    """Serve the default agent's tools to external MCP clients at `POST /mcp` (streamable HTTP)."""

    # --- M5f
    locale: str | None = None
    """User locale (e.g. `de-DE`), rendered as `{{locale}}` in the per-turn context block."""
    # --- M5i
    sandbox_policy: Literal["warn", "fail"] = "warn"
    """When the selected sandbox backend fails its startup probe: `warn` logs, audits
    `sandbox.fallback` and uses `none`; `fail` refuses to start."""
    sandbox_docker_image: str = "python:3.12-slim"
    """Image for the docker sandbox backend (pulled on first use)."""
    sandbox_docker_mem_limit: str = "1g"
    """Memory limit for docker sandbox containers (Docker syntax: `512m`, `2g`)."""
    db_query_dsns: dict[str, str] = Field(default_factory=dict)
    """Allow-listed DSNs for `db_query`: name → `secret:<name>` or a SQLAlchemy URL
    (e.g. `sqlite:///path/app.db`). Only names listed here can be queried."""
    # --- M4c
    voice_enabled: bool = True
    """Start the voice service (`/api/voice/*`, `transcribe`/`speak` tools, RTC transcription)."""
    voice_download_models: bool = False
    """Let local voice backends (faster-whisper, KittenTTS) download model weights on first use."""
    voice_language: str | None = None
    """Default STT language (ISO-639-1); None = auto-detect."""
    voice_tts_voice: str | None = None
    """Default TTS voice (backend-specific name or id)."""
    voice_speaking_rms: float = 0.02
    """Normalised RMS (0..1) above which the fake VAD counts a 30 ms frame as speech."""
    voice_stt_model: str | None = None
    """Model override for the selected STT backend (e.g. `small`, `whisper-1`)."""
    voice_endpoint_silence_ms: int = Field(default=700, ge=100, le=5000)
    """Silence after speech that ends a live (WebRTC) utterance."""
    voice_wake_phrase: str = "hey oreo"
    voice_kws_model_dir: Path | None = None
    """sherpa-onnx keyword-spotting model folder (tokens.txt, encoder/decoder/joiner .onnx)."""
    # --- M4d
    cua_driver_command: str = "cua-driver"
    """Executable of the cua-driver MCP server (`<command> mcp` over stdio)."""
    cua_telemetry: bool = False
    """Leave cua-driver telemetry on (default forces `CUA_DRIVER_RS_TELEMETRY_ENABLED=0`)."""
    # --- W0-C
    suggestions_enabled: bool = True
    """Proactive suggestions (consent-first; never executed without an explicit accept)."""
    suggestions_home: str = "log"
    """Delivery target (`<provider>[:<target>]`) for `deliver: home` and the failure fallback."""
    suggestions_quiet_hours: str | None = Field(default=None, pattern=r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$")
    """Local-time window like `22:00-07:00` during which suggestion delivery is deferred."""
    suggestions_max_pending: int = Field(default=5, ge=1, le=100)
    """At most this many pending suggestions; proposing more expires the oldest."""
    # --- W0-D
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L6-v2"
    """Model for the selected rerank backend: a Hugging Face cross-encoder id for
    `cross_encoder` (default MiniLM-L6: ~200 ms for 30 pairs on CPU; `BAAI/bge-reranker-v2-m3`
    is multilingual but ~2 s on CPU, so raise `rerank_timeout_s` or use a GPU), a provider model for `cohere`
    (`rerank-v3.5` when this is left at the cross-encoder default)."""
    rerank_download_models: bool = False
    """Let the local cross-encoder download weights on first use (else it must be cached)."""
    rerank_timeout_s: float = Field(default=2.0, gt=0, le=60)
    """Per-query rerank budget; on timeout the RRF order is kept (fail-open)."""
    rerank_top_n: int = Field(default=8, ge=1, le=30)
    """Hits kept after reranking the top-30 window (spec §24.3: top-8)."""
    otel_mode: Literal["off", "local", "otlp"] = "off"
    """`AUOREO_OTEL_MODE`: off · local (console, or in-memory with AUOREO_OTEL_TEST=1) · otlp
    (OTLP/HTTP to OTEL_EXPORTER_OTLP_ENDPOINT). Needs the `otel` extra."""
    otel_capture_content: bool = False
    """Attach prompt/tool bodies to spans. Off by default (spec §20.1)."""
    # --- W0-E
    lsp_timeout_ms: int = Field(default=5000, ge=100, le=600_000)
    """Per-request timeout for the `lsp` tool's language-server calls."""
    lsp_servers: dict[str, list[str]] = Field(default_factory=dict)
    """Language-server argv overrides by language (`python`, `typescript`, `javascript`)."""
    shell_max_processes: int = Field(default=8, ge=1, le=64)
    """Live `exec_command` processes allowed per session."""
    # --- W1-B
    teams_orchestrator_enabled: bool = False
    """Supervisor opt-in (spec §16.3): a coordinator (or a child spawned with
    `role=supervisor`) must pass `output_schema` on every spawn and its continue/spawn/finish
    decisions are logged as `team.decision` events. Off: `role=supervisor` is refused.
    Depth 3 (supervisor → worker → helper) stays opt-in via `teams_max_depth=3`."""
    # --- W1-C
    bandit: BanditSettings = Field(default_factory=BanditSettings)
    """Model/effort policy (spec §12.5): contextual epsilon-greedy, default off."""
    # --- W1-E
    db_encryption: Literal["off", "columns", "sqlcipher"] = "off"
    """Data at rest (spec L1069). `columns`: Fernet on `events.data` + `audit.detail` (key
    `auoreo-db-key` in the keyring or a 0600 `.db.key`); `sqlcipher`: whole-file state.db
    encryption (needs the `sqlcipher` extra and `auoreo db encrypt` for an existing file)."""
    redis_url: str | None = None
    """Redis for multi-worker event fan-out (`auoreo:events` pub/sub); needs the `redis` extra."""
    workers: int = Field(default=1, ge=1, le=64)
    """Uvicorn worker processes. >1 refuses to start without `redis_url`; sticky sessions
    are required at the load balancer (approvals, WS connections and runs are per process)."""
    # --- W2-A
    environments: dict[str, NamedEnvironment] = Field(default_factory=dict)
    """Named execution environments a session can switch to with `use_environment`; the
    default is `backends.environment` (the `local` backend unless set)."""
    environment_docker_image: str = "python:3.12-slim"
    environment_docker_network: str = "none"
    """`network_mode` of the per-session container (`none`, `bridge`, a network name)."""
    environment_docker_mem_limit: str = "2g"
    environment_ssh: SshEnvironment | None = None
    environment_e2b_template: str = "base"
    environment_modal_app: str = "auoreo-environments"
    environment_modal_image: str = "python:3.12-slim"
    environment_daytona_api_url: str = "https://app.daytona.io/api"
    environment_daytona_snapshot: str | None = None
    environment_sandbox_timeout_s: int = Field(default=3600, ge=60, le=86_400)
    """Lifetime of a cloud sandbox (E2B / Modal / Daytona); it is also destroyed at session close."""
    # --- W2-C
    detection_model: str = "nano"
    """RF-DETR size for `backends/detection/rfdetr`: nano|small|medium|large (Apache-2.0).
    `xlarge`/`2xlarge` (PML-1.0 weights) are refused by the backend's `check()`."""
    perception_download_models: bool = False
    """Let local perception backends (RF-DETR, Grounding-DINO, MediaPipe) download weights."""
    faces_enabled: bool = False
    """Face detection opt-in (spec L744). Off: `detect_faces` refuses. Embeddings are never
    persisted, only returned to the caller that asked for them."""
    face_nc_opt_in: bool = False
    """Allow the non-commercial InsightFace backend (its weights are NC-licensed)."""
    yolo_sidecar_url: str | None = None
    """Base URL of the AGPL YOLO sidecar process (`backends/detection/yolo_onnx`); never in-process."""
    perception_sample_rtc_video: bool = False
    """Sample one frame every `perception_rtc_interval_s` from RTC video tracks into the
    in-memory `rtc` sensor source (frames are never written to disk)."""
    perception_rtc_interval_s: float = Field(default=5.0, ge=0.5, le=3600)
    # --- W2-B: remote plane (daemon, exec-server client, instance bridges)
    environment_remote: RemoteEnvironment | None = None
    """`backends/environment/remote`: `{url: "ws://127.0.0.1:7071", token: "secret:<name>"}`."""
    bridges: dict[str, BridgePeer] = Field(default_factory=dict)
    """Peer instances for mailbox `bridge:<name>/<member>` addressing (HMAC-signed)."""
    instance_name: str = Field(default="auoreo", pattern=r"^[A-Za-z0-9_.-]{1,32}$")
    """How this instance names itself to bridge peers (overridable per peer: `as_name`)."""
    # --- W2-D
    livekit: LiveKitSettings = Field(default_factory=LiveKitSettings)
    """LiveKit SFU transport (`backends.rtc_transport = "livekit"`, extra `livekit`); P2 opt-in."""

    # --- W3-A
    update_channel: Literal["stable", "beta", "dev"] = "stable"
    """Default channel for `auoreo upgrade` (`--channel` wins)."""
    update_url: str = (
        "https://github.com/Raiyan007-gb/auoreo/releases/download/updates/manifest-{channel}.json"
    )
    """Signed update manifest; `{channel}` is substituted. Self-hosters point this at their own."""
    update_pubkeys: list[str] = Field(default_factory=list)
    """Extra base64 raw ed25519 keys trusted for manifests, in addition to the pinned release key."""
    # --- F1: agent speaks back over WebRTC, barge-in, echo guard, teleop input
    voice_speak_over_rtc: bool = True
    """Speak final assistant replies on an outbound audio track of each RTC peer (needs
    `voice_enabled`; a peer can opt out with `speak: false` in its offer)."""
    voice_speak_max_chars: int = Field(default=1500, ge=1, le=5000)
    """Longer replies are cut at a sentence/word boundary and end with "…"; the rest stays text."""
    voice_barge_in_interrupts_turn: bool = False
    """Barge-in also cancels the running turn (default: only the spoken output is cut)."""
    voice_echo_margin: float = Field(default=2.0, ge=1.0, le=20.0)
    """VAD threshold multiplier while outbound audio plays and for 300 ms after."""
    voice_echo_corr: float = Field(default=0.8, gt=0.0, le=1.0)
    """Drop inbound frames whose normalised correlation with the last 1 s of outbound audio exceeds this."""
    teleop_input_enabled: bool = False
    """Accept the `auoreo-input` data channel (remote click/type/key/scroll through `computer`)."""

    # --- F2
    github_allowed_repos: list[str] = Field(default_factory=list)
    """`gh_*` tools: extra repos (globs like `acme/*`) besides the cwd checkout's own remote."""
    plan_requires_verify: bool = False
    """`exit_plan_mode` refuses until `verify_plan` passed for the current plan."""

    # --- F3
    sentry_dsn: str | None = None
    """`AUOREO_SENTRY_DSN`: opt-in error reporting (extra `sentry`). Unset = no Sentry at all."""
    sentry_share: Literal["full", "errors_only", "off"] = "errors_only"
    """`errors_only`: redacted exceptions, no breadcrumbs or traces; `full`: also breadcrumbs
    and (only when `otel_mode` is on) performance traces; `off`: every event is dropped."""
    trace_file: Path | None = None
    """`AUOREO_TRACE_FILE`: write a Chrome/Perfetto Trace-Event JSON file (works with OTel off)."""

    # --- F4
    voice_meeting_keep_audio: bool = False
    """Keep a meeting's uploaded audio chunks after finalise (default: deleted)."""
    voice_meeting_ingest: bool = True
    """Ingest each finalised (redacted) meeting transcript into memory as a document."""
    voice_diarize_segmentation_model: Path | None = None
    """`diarize/sherpa_onnx`: pyannote segmentation-3.0 `model.onnx` path."""
    voice_diarize_embedding_model: Path | None = None
    """`diarize/sherpa_onnx`: speaker-embedding `.onnx` path."""
    voice_diarize_num_speakers: int | None = Field(default=None, ge=1, le=32)
    """Known speaker count for diarization (unset = estimate by clustering threshold)."""
    voice_diarize_threshold: float = Field(default=0.5, gt=0.0, le=2.0)
    """`diarize/sherpa_onnx` clustering threshold when the speaker count is unknown."""
    tone_enabled: bool = True
    """Classify the user's tone each turn and render it as `{{tone}}` in the context block."""
    tone_adaptive: bool = False
    """Affect-adaptive guidance line when the tone is frustrated/confused (confidence ≥ 0.7)."""
    # --- F5: ephemeral TURN (TURN REST API) for the aiortc path
    turn_secret_ref: str | None = Field(default=None, pattern=r"^secret:[A-Za-z0-9_.-]+$")
    """`secret:<NAME>`: the coturn `static-auth-secret`; `GET /api/rtc/ice` and the server peer
    derive time-limited credentials from it (the secret itself is never returned)."""
    turn_urls: list[str] = Field(default_factory=list)
    """`turn:`/`turns:` URLs the ephemeral credentials are valid for (e.g. `turns:turn.example.com:5349`)."""
    turn_ttl_s: int = Field(default=3600, ge=60, le=86_400)
    # --- F6: out-of-process voice media sidecar (`backends.{stt,tts,vad} = "sidecar"`)
    voice_host_python: str | None = None
    """Interpreter for `python -m auoreo.voice.host` (e.g. a GPU venv with auoreo + torch);
    unset = this server's own interpreter."""
    voice_host_backends: VoiceHostBackends = Field(default_factory=VoiceHostBackends)
    """Inner stt/tts/vad backends the sidecar loads (defaults: fake)."""
    voice_host_timeout_s: float = Field(default=60.0, gt=0, le=3600)
    """Per-request timeout for sidecar calls (a timed-out request fails; the process keeps running)."""


class _JsonFileSource(PydanticBaseSettingsSource):
    """Reads `<home>/settings.json`; malformed file warns and is ignored."""

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        path = home_dir() / "settings.json"
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("ignoring unreadable %s: %s", path, exc)
            return {}
        return data if isinstance(data, dict) else {}


def write_settings_value(home: Path, dotted: str, value: Any) -> Path:
    """Set `a.b.c` in `<home>/settings.json` (atomic write)."""
    path = home / "settings.json"
    data: dict[str, Any] = {}
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    node = data
    *parents, leaf = dotted.split(".")
    for key in parents:
        node = node.setdefault(key, {})
    node[leaf] = value
    home.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


__all__ = [
    "PermissionMode",
    "Settings",
    "get_settings",
    "write_settings_value",
    "LiveKitSettings",
    "LiveKitSipSettings",
    "LoopSettings",
    "MemorySettings",
    "ModelGroup",
    "NamedEnvironment",
    "SshEnvironment",
    "VoiceHostBackends",
]
