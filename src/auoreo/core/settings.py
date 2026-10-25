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

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from auoreo.core.paths import home_dir

log = logging.getLogger(__name__)

PermissionMode = Literal["manual", "smart", "yolo", "plan", "acceptEdits"]


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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUOREO_", extra="ignore")

    home: Path = Field(default_factory=home_dir)
    host: str = "127.0.0.1"
    port: int = 4096
    server_password: str | None = None
    gateway: Literal["litellm", "fake"] = "litellm"
    model: str | None = None
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
