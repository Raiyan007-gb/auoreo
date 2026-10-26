"""Generate `.env.example` from the Settings model plus the provider, tool and backend keys.

    uv run python scripts/gen_env_example.py --check   # CI: fail when .env.example drifts
    uv run python scripts/gen_env_example.py --write   # after adding a setting or a key

Tags come from the real loader (`core.dotenv`), so `[home]` / `[env-only]` can never disagree with
what a project `.env` is actually allowed to set.
"""

from __future__ import annotations

import json
import sys
import types
import typing
from pathlib import Path

from pydantic import BaseModel

from auoreo.core.dotenv import is_protected, project_allowed
from auoreo.core.settings import Settings

FIELDS = Settings.model_fields
done: set[str] = set()
out: list[str] = []
w = out.append


def default_of(name: str) -> str:
    f = FIELDS[name]
    if name == "home":
        return "~/.auoreo (Windows %LOCALAPPDATA%\\auoreo)"
    if f.default_factory is not None:
        try:
            v = f.default_factory()  # type: ignore[call-arg]
        except TypeError:
            return "see settings.json"
        if v in ({}, []):
            return "{}" if isinstance(v, dict) else "[]"
        return (
            "built-in (see settings.json)"
            if isinstance(v, dict) and any(isinstance(x, BaseModel) for x in v.values())
            else json.dumps(v, default=str)
        )
    v = f.default
    if v is None:
        return "unset"
    if isinstance(v, bool):
        return str(v).lower()
    return str(v)


def tier(env: str) -> str:
    """Where the loader accepts `env`: anywhere, `~/.auoreo/.env` only, or the real env only."""
    if is_protected(env):
        return "env-only"
    return "" if project_allowed(env) else "home"


TAGS = {"env-only": "  [env-only]", "home": "  [home]", "": ""}


def _kind(name: str) -> str:
    """'' for scalars, 'JSON' for dict/list values, 'JSON object' for nested settings groups."""
    ann = FIELDS[name].annotation
    is_union = typing.get_origin(ann) in (typing.Union, types.UnionType)
    args = typing.get_args(ann) if is_union else (ann,)
    flat = [typing.get_origin(a) or a for a in args]
    if any(isinstance(a, type) and issubclass(a, BaseModel) for a in flat):
        return "JSON object"
    if any(a in (dict, list) for a in flat):
        return "JSON"
    return ""


def setting(name: str, note: str = "", value: str = "") -> None:
    assert name in FIELDS, name
    assert name not in done, name
    done.add(name)
    env = f"AUOREO_{name.upper()}"
    tag = TAGS[tier(env)]
    kind = _kind(name)
    if kind == "JSON object":
        dflt = "JSON object; settings.json key `" + name + "`"
    else:
        dflt = f"default {default_of(name)}" + (" (JSON)" if kind else "")
    line = "# " + " · ".join(p for p in (note, dflt) if p) + tag
    if len(line) > 100:  # keep comments readable: the tag/default go on their own line
        w("# " + note)
        line = "# " + dflt + tag
    w(line)
    w(f"{env}={value}")


def secret(env: str, note: str) -> None:
    w(f"# {note}{TAGS[tier(env)]}")
    w(f"{env}=")


def section(title: str) -> None:
    w("")
    w(f"# {'-' * (90 - len(title) - 3)} {title}")


HEADER = """# AUOREO — every environment variable AUOREO reads. Copy to `~/.auoreo/.env` (recommended) or a
# project `.env`, then fill in only what you use: a blank value is ignored (the built-in default
# applies), and a variable already set in the real environment always wins.
#
# Where a variable may live:
#   (no tag)    anywhere: a project `.env`, `~/.auoreo/.env`, or the real environment
#   [home]      `~/.auoreo/.env` or the real environment. A PROJECT `.env` is repository-controlled,
#               so it may only set provider keys and AUOREO_MODEL / SMALL_MODEL / EMBED_MODEL /
#               EMBED_DIMS / AGENT / WORKFLOW / GATEWAY / PORT; anything else (and any *_API_BASE,
#               *_URL, *_HOST, *_ENDPOINT, proxy, PATH-like name) is ignored there with a warning.
#   [env-only]  security knobs: read ONLY from the real process environment (`export ...`); both
#               `.env` files ignore them with a warning, so a checked-out repo can never set them.
#
# Every AUOREO_* below is also a `settings.json` key (`~/.auoreo/settings.json`, same name in
# lower case without the prefix); the environment wins over settings.json. "(JSON)" values take a
# JSON literal, e.g. AUOREO_CORS_ORIGINS='["https://app.example.com"]'. Nested groups such as
# AUOREO_LIVEKIT take a JSON object; settings.json is the friendlier place for those.
#
# Keys can live in the encrypted store instead of a file:  auoreo secrets set ANTHROPIC_API_KEY
# Lookup order for a key: real environment → keyring/secrets.db → `.env` values."""


def body() -> None:
    w(HEADER)

    section("models")
    w("""# Universal rule: set the key for your provider, then name the model as  <provider>/<model id>
# The provider prefix picks the key (LiteLLM naming); everything after the first `/` is the
# provider's own model id. Examples:
#
#   key                          AUOREO_MODEL
#   OPENROUTER_API_KEY=...       openrouter/anthropic/claude-sonnet-4.5   (any OpenRouter id)
#   ANTHROPIC_API_KEY=...        anthropic/claude-sonnet-5
#   OPENAI_API_KEY=...           openai/gpt-5
#   GEMINI_API_KEY=...           gemini/gemini-2.5-pro
#   GROQ_API_KEY=...             groq/llama-3.3-70b-versatile
#   XAI_API_KEY=...              xai/grok-4
#   MISTRAL_API_KEY=...          mistral/mistral-large-latest
#   DEEPSEEK_API_KEY=...         deepseek/deepseek-chat
#   TOGETHERAI_API_KEY=...       together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo
#   FIREWORKS_AI_API_KEY=...     fireworks_ai/accounts/fireworks/models/<model>
#   COHERE_API_KEY=...           cohere/command-r-plus
#   (no key)                     ollama/llama3.2            (OLLAMA_API_BASE=http://localhost:11434)
#   AZURE_API_KEY + AZURE_API_BASE + AZURE_API_VERSION     azure/<deployment name>
#
# Aliases: `best`, `sonnet`, `opus`, `sonnet[1m]` / `opus[1m]` (1M-context deployments).
# `auoreo doctor` prints one credentials row per configured model and names any missing key.""")
    setting("model", "main model, <provider>/<model id>", value="openrouter/anthropic/claude-sonnet-4.5")
    setting("small_model", "cheaper model for titles / compaction / guardian; unset = AUOREO_MODEL")
    setting("embed_model", "embedding model for long-term memory; unset = keyword search only")
    setting("embed_dims", "embedding dimensions (only for models that let you choose)")
    setting(
        "models",
        'named model groups, e.g. {"default":{"deployments":[{"model":"anthropic/claude-sonnet-5"}]}}',
    )
    setting("aliases", 'extra model aliases, e.g. {"fast":"groq/llama-3.3-70b-versatile"}')
    setting("fallbacks", 'per-group fallback chains, e.g. {"default":["openai/gpt-5"]}')
    setting("max_cost_usd", "per-session spend cap in USD (sub-agents count toward the root)")
    setting("auxiliary", "aux model routing per background task (title, compaction, guardian, …)")
    setting("gateway_tuning", "retries / timeouts / cooldowns of the LiteLLM Router")
    setting("i2", "gateway extras: key pools, daily budgets, structured output, OpenRouter lane, deployments")
    setting("bandit", "opt-in model/effort bandit policy (off by default)")

    section("provider keys (fill in only the ones you use)")
    for env, note in (
        ("OPENROUTER_API_KEY", "OpenRouter — openrouter/<vendor>/<model>"),
        ("ANTHROPIC_API_KEY", "Anthropic — anthropic/<model>"),
        ("OPENAI_API_KEY", "OpenAI — openai/<model>; also OpenAI STT/TTS/Realtime and image generation"),
        ("GEMINI_API_KEY", "Google Gemini — gemini/<model>"),
        ("GROQ_API_KEY", "Groq — groq/<model>; also Groq STT"),
        ("XAI_API_KEY", "xAI — xai/<model>; also xAI STT (experimental)"),
        ("MISTRAL_API_KEY", "Mistral — mistral/<model>; also Mistral STT/TTS"),
        ("DEEPSEEK_API_KEY", "DeepSeek — deepseek/<model>"),
        ("TOGETHERAI_API_KEY", "Together AI — together_ai/<model>"),
        ("FIREWORKS_AI_API_KEY", "Fireworks AI — fireworks_ai/<model>"),
        ("COHERE_API_KEY", "Cohere — cohere/<model>; also the `cohere` memory rerank backend"),
        ("AZURE_API_KEY", "Azure OpenAI — azure/<deployment> (with AZURE_API_BASE + AZURE_API_VERSION)"),
        ("AZURE_API_VERSION", "Azure OpenAI API version, e.g. 2025-04-01-preview"),
    ):
        secret(env, note)
    secret("AZURE_API_BASE", "Azure OpenAI endpoint, https://<resource>.openai.azure.com")
    secret("OLLAMA_API_BASE", "Ollama server for ollama/<model> (default http://localhost:11434)")

    section("tool and backend keys (only for the features you enable)")
    for env, note in (
        ("EXA_API_KEY", "web_search: Exa provider"),
        ("PARALLEL_API_KEY", "web_search: Parallel provider"),
        ("BRAVE_API_KEY", "web_search: Brave provider"),
        ("TAVILY_API_KEY", "web_search: Tavily provider"),
        ("JINA_API_KEY", "web_search / web_fetch: Jina search + reader"),
        ("FIRECRAWL_API_KEY", "web_search / web_fetch: Firecrawl provider"),
        ("GH_TOKEN", "gh_* tools: GitHub token (or run `gh auth login` once)"),
        ("GITHUB_TOKEN", "gh_* tools: alternative name for the GitHub token"),
        ("DEEPGRAM_API_KEY", "voice: Deepgram streaming STT"),
        ("DEEPINFRA_API_KEY", "voice: DeepInfra STT"),
        ("ELEVENLABS_API_KEY", "voice: ElevenLabs STT/TTS"),
        ("PICOVOICE_ACCESS_KEY", "voice: Porcupine wake word (PORCUPINE_ACCESS_KEY also accepted)"),
        ("HF_TOKEN", "voice: Hugging Face token for the pyannote diarization models"),
        ("FAL_KEY", "imagegen plugin: fal.ai FLUX provider"),
        ("E2B_API_KEY", "execution environment: E2B sandboxes"),
        ("MODAL_TOKEN_ID", "execution environment: Modal token id"),
        ("MODAL_TOKEN_SECRET", "execution environment: Modal token secret"),
        ("DAYTONA_API_KEY", "execution environment: Daytona sandboxes"),
        ("LIVEKIT_API_KEY", "LiveKit SFU / SIP / agent worker (server URL goes in AUOREO_LIVEKIT)"),
        ("LIVEKIT_API_SECRET", "LiveKit API secret"),
        ("MQTT_PASSWORD", "mqtt plugin: broker password (name configurable via password_secret)"),
    ):
        secret(env, note)
    w("# Passed through to the `gh` CLI behind the gh_* tools (standard names; usually unset):")
    for env, note in (
        ("GH_HOST", "GitHub Enterprise host for gh_* tools, e.g. github.example.com"),
        ("GH_CONFIG_DIR", "alternative gh config directory"),
        ("HTTPS_PROXY", "outbound proxy for the gh CLI"),
        ("NO_PROXY", "hosts that bypass HTTPS_PROXY"),
        ("SSL_CERT_FILE", "CA bundle for the gh CLI"),
    ):
        secret(env, note)
    w("""# Secrets with a `/` in the name cannot be environment variables; store them with
#   auoreo secrets set <name>
#   telegram/bot_token          Telegram connector + delivery
#   discord/bot_token           Discord gateway connector (inbound, approvals)
#   discord/webhook_url         Discord delivery via webhook
#   email/password              email plugin (IMAP/SMTP password; or use `auoreo login google`)
#   calendar/password           calendar plugin (CalDAV)
#   oauth/google_client_secret  Google OAuth client secret (connectors/google/oauth.yaml)
#   webhook/<route>             HMAC secret of webhooks/<route>.yaml (`auoreo webhook add` makes one)
#   provider/<name>/<slot>      extra keys for a provider key pool (i2.key_pools)""")

    section("server")
    setting("gateway", "litellm (real models) | fake (deterministic, keyless)", value="litellm")
    setting("host", "bind address; anything beyond loopback needs AUOREO_SERVER_PASSWORD")
    setting("port", "HTTP + WebSocket port", value="4096")
    setting("server_password", "required for non-loopback binds; browsers get a short-lived JWT cookie")
    setting("agent", "agent that answers when none is named", value="default")
    setting("workflow", "workflow used for plain chat turns", value="chat")
    setting("cors_origins", "extra allowed browser origins")
    setting("ws_ping_interval_s", "WebSocket keepalive ping")
    setting("instance_name", "name shown to bridged instances")
    setting("locale", "UI / prompt locale, e.g. en-US")
    setting("workers", "uvicorn workers; >1 requires AUOREO_REDIS_URL")
    setting("redis_url", "Redis for multi-worker event fan-out, redis://host:6379/0")
    setting("mcp_server_enabled", "serve AUOREO itself as an MCP server at /mcp")
    setting("http", "Host allowlist, trusted proxies, cookie + queue limits")
    setting("cli", "serve flags: ws, tunnel, trusted hosts, mDNS, ws_auth (token|jwt|capability|off)")
    setting("cli2", "loop / heartbeat / worktree gc / debug upload endpoint")
    setting("h1b", "JWT session cookies, capability tokens, keyring policy on public binds")
    setting("i5", "web UI/CLI extras: share links, skills hub UI, doctor --fix, stream-json guard")

    section("security (real environment only)")
    w("""# These are frozen from the real process environment at startup so prompt-injected repos can't
# loosen them. Set them with `export` (or your service manager), never in a .env file.""")
    setting("permission_mode", "manual | smart | yolo | plan | acceptEdits (yolo also needs AUOREO_YOLO=1)")
    secret("AUOREO_YOLO", "set to 1 to allow permission_mode=yolo (floors still apply)")
    setting("smart_timeout", "smart mode: guardian review budget in seconds (else i9.guardian.timeout_s, 15)")
    w("""# approvals.smart_policy: there is no separate knob — the guardian policy text is the
# `prompts/guardian.md` section (role: guardian); override it in ~/.auoreo/prompts/ and keep
# approvals.unattended (approve|deny per single_query/cron/platform) for no-human runs.""")
    setting("approvals", "unattended approval policy per run kind: single_query / cron / platform")
    setting("approval_timeout_s", "how long an approval card waits for you")
    setting("security", "redaction modes, sensitive writes, sandbox Landlock layer, trust settings")
    setting("floors", "hardline-floor knobs (oversized inline command threshold, …)")
    setting("redact_secrets", "mask secrets in logs, events and tool output")
    secret("AUOREO_REDACT_PHONES", "set to 1 to also mask phone numbers")
    setting("keyring", "require | allow-file | forbid-file — where the secrets-encryption key may live")
    setting("backends", 'backend per kind, e.g. {"sandbox":"bwrap","stt":"openai"}; sandbox default auto')
    setting("sandbox_policy", "warn | fail when the selected sandbox cannot start")
    setting("sandbox_docker_image", "image for the docker sandbox backend")
    setting("sandbox_docker_mem_limit", "memory limit for the docker sandbox backend")
    setting("allow_private_urls", "let web tools reach private / loopback addresses (SSRF floor off)")
    setting("webhooks", "webhook ingress: strict_secrets (default true), size limits")
    setting("extra_roots", "extra definition roots (agents/tools/skills …) after the bundled one")
    secret("AUOREO_HOME", "runtime home, default ~/.auoreo (Windows %LOCALAPPDATA%\\auoreo)")
    secret("AUOREO_PROFILE", "profile name: uses <home>/profiles/<name>/ (same as --profile)")
    done.add("home")

    section("database and secrets at rest")
    setting("db_encryption", "off | columns | sqlcipher")
    setting(
        "db_query_dsns", 'named DSNs for the db_query tool, values as secret refs: {"prod":"secret:prod_dsn"}'
    )

    section("sessions, compaction, memory")
    setting("loop", "agent-loop limits (steps, budgets, doom-loop guards)")
    setting("compaction", "compaction thresholds, idle compaction, memory nudges, query rewrite")
    setting("i4", "compaction extras, memory scopes/tenants/decay, re-embed, CJK search")
    setting("memory", "long-term memory: embed model, recall, HyDE, graph, chunking")
    setting("rerank_model", "local cross-encoder for memory rerank (backends.rerank)")
    setting("rerank_download_models", "allow downloading rerank weights")
    setting("rerank_timeout_s", "")
    setting("rerank_top_n", "")
    setting("strict_overlays", "fail (not warn) when a user overlay shadows a bundled definition")
    setting("plan_requires_verify", "exit_plan_mode requires a verify_plan PASS first")
    setting("suggestions_enabled", "consent-first proactive suggestions")
    setting("suggestions_home", "delivery target for suggestions (backends/delivery/<name>)")
    setting("suggestions_quiet_hours", "e.g. 22:00-07:00")
    setting("suggestions_max_pending", "")

    section("tools, shell, search, LSP")
    setting("tools", "tool knobs: PTY/jobs limits, apply_patch LSP diagnostics, doc caps, pandas")
    setting("tool_engine", "check_fn TTL, collision policy, spill cap, MCP sampling limits")
    setting("h5", "apply_patch formatter step, run_code / video_analyze limits, metric store")
    setting("i9", "egress proxy (allow/deny hosts), search provider order + cache, guardian timeout")
    setting("searxng_url", "self-hosted SearXNG for web_search, http://localhost:8888")
    setting("shell_max_processes", "concurrent persistent shells per session")
    setting("lsp_timeout_ms", "")
    setting("lsp_servers", 'language-server commands, e.g. {"python":["uvx","ty","server"]}')
    setting("browser_executable_path", "Chromium/Chrome binary for the browser backend")
    setting("github_allowed_repos", 'repos the gh_* tools may touch, e.g. ["owner/repo"]')

    section("automation, teams, plugins, MCP")
    setting("hooks_enabled", "")
    setting("scheduler_enabled", "run jobs/*.yaml on their schedules")
    setting("webhooks_enabled", "accept POST /hooks/<route>")
    setting("automation_workers", "")
    setting("automation_start_delay_s", "")
    setting("automation_poll_s", "")
    setting("automation_run_timeout_s", "")
    setting("teams_max_depth", "delegation depth limit")
    setting("teams_max_children", "concurrent children per parent")
    setting("teams_child_timeout_s", "")
    setting("teams_orchestrator_enabled", "opt-in supervisor that can spawn at depth 2")
    setting("teams2", "delegation pause, heartbeat/stale timings, job timeout, chat approvals")
    setting("i3", "kanban dispatcher (off by default), cron extras, delegation naming")
    setting("plugins_enabled", "")
    setting("plugins", 'per-plugin config, e.g. {"telegram":{"enabled":true,"owners":["123"]}}')
    setting("plugins_python", "Python for plugin venvs")
    setting("plugins_seed", "copy bundled plugins into ~/.auoreo/plugins on first start")
    setting("plugins_poll_s", "")
    setting("plugins_tool_timeout_s", "")
    setting("plugins_backoff_scale", "")
    setting("i6", "plugin trust tiers/taps/allow-list, MCP roots + sampling, GitHub /meta CIDRs")
    setting("g6", "voice extras, PREFS proposals, skills hub taps, review worktrees")

    section("voice and WebRTC")
    setting("voice_enabled", "")
    setting("voice_download_models", "allow downloading local STT/TTS/wake models")
    setting("voice_language", "STT language hint, e.g. en")
    setting("voice_stt_model", "STT model id for the selected stt backend")
    setting("voice_tts_voice", "TTS voice id for the selected tts backend")
    setting("voice_speaking_rms", "")
    setting("voice_endpoint_silence_ms", "silence that ends an utterance")
    setting("voice_wake_phrase", "")
    setting("voice_kws_model_dir", "keyword-spotting model folder (sherpa-onnx)")
    setting("voice_speak_over_rtc", "")
    setting("voice_speak_max_chars", "")
    setting("voice_barge_in_interrupts_turn", "")
    setting("voice_echo_margin", "")
    setting("voice_echo_corr", "")
    setting("voice_meeting_keep_audio", "")
    setting("voice_meeting_ingest", "")
    setting("voice_diarize_segmentation_model", "")
    setting("voice_diarize_embedding_model", "")
    setting("voice_diarize_num_speakers", "")
    setting("voice_diarize_threshold", "")
    setting("voice_host_python", "Python for the out-of-process voice sidecar")
    setting("voice_host_timeout_s", "")
    setting("voice_host_backends", "backends the voice sidecar runs (stt/tts/wake)")
    setting("tone_enabled", "")
    setting("tone_adaptive", "")
    setting("i8", "wake daemon, STT filters/keyterms, LiveKit worker, computer-use safety, vision")
    setting("rtc_max_peers", "")
    setting("rtc_ice_servers", 'STUN/TURN URLs, e.g. ["stun:stun.l.google.com:19302"]')
    setting("rtc_audio_level_interval_s", "")
    setting("rtc_speaking_rms", "")
    setting("turn_urls", 'your TURN server URLs, e.g. ["turns:turn.example.com:443"]')
    setting("turn_secret_ref", "secret name holding the TURN shared secret (auoreo secrets set …)")
    setting("turn_ttl_s", "")
    setting("livekit", 'LiveKit server, e.g. {"url":"wss://lk.example.com"} (keys: LIVEKIT_API_KEY/SECRET)')
    setting("teleop_input_enabled", "allow remote keyboard/mouse input over WebRTC")

    section("computer use and perception")
    setting("cua_driver_command", "desktop driver binary")
    setting("cua_telemetry", "")
    setting("detection_model", "object detection size: nano | small | medium | base | large")
    setting("perception_download_models", "allow downloading detection/face models")
    setting("faces_enabled", "face detection (off by default; embeddings never stored)")
    setting("face_nc_opt_in", "opt in to non-commercial face models")
    setting("yolo_sidecar_url", "AGPL-isolated YOLO sidecar, http://127.0.0.1:<port>")
    setting("perception_sample_rtc_video", "")
    setting("perception_rtc_interval_s", "")

    section("execution environments and remote plane")
    setting("environments", 'named environments, e.g. {"gpu":{"backend":"ssh","host":"gpu.local"}}')
    setting("environment_docker_image", "")
    setting("environment_docker_network", "")
    setting("environment_docker_mem_limit", "")
    setting("environment_ssh", '{"host":"…","user":"…","key_secret":"ssh_key"}')
    setting("environment_e2b_template", "")
    setting("environment_modal_app", "")
    setting("environment_modal_image", "")
    setting("environment_daytona_api_url", "")
    setting("environment_daytona_snapshot", "")
    setting("environment_sandbox_timeout_s", "")
    setting("environment_remote", '{"url":"ws://host:port","token_secret":"exec_token"} for an exec-server')
    setting("bridges", "named peer instances for the signed instance bridge")
    secret("AUOREO_URL", "CLI: talk to this running server instead of working in-process")
    secret("AUOREO_EXEC_TOKEN", "`auoreo exec-server` bearer token (prefer --token-secret)")

    section("observability")
    setting("logging", "log format text | json, level")
    setting("otel_mode", "off | local | otlp")
    setting("otel_capture_content", "include prompts/outputs in spans (off: metadata only)")
    secret("OTEL_EXPORTER_OTLP_ENDPOINT", "OTLP collector for otel_mode=otlp, http://localhost:4318")
    secret("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "traces-only OTLP endpoint (overrides the one above)")
    secret("OTEL_EXPORTER_OTLP_HEADERS", "OTLP auth headers, key=value,key2=value2")
    secret("OTEL_TRACES_SAMPLER_ARG", "trace sampling ratio 0..1")
    setting("sentry_dsn", "Sentry DSN (needs the sentry extra)")
    setting("sentry_share", "full | errors_only | off")
    setting("trace_file", "write a Perfetto/Chrome trace JSON here")

    section("updates")
    setting("update_channel", "stable | beta | dev")
    setting("update_url", "signed update manifest URL template")
    setting("update_pubkeys", "extra ed25519 public keys trusted for update manifests")

    section("developer and CI (leave blank for normal use)")
    secret("AUOREO_OFFLINE", "set to 1 to skip network probes in doctor and backends")
    secret("AUOREO_CODE_SHA", "override the code hash reported by /api/health")
    secret("AUOREO_ALLOW_NO_WEB_DIST", "set to 1 to build a wheel without web/dist")
    secret("UPDATE_SIGNING_KEY", "release CI: ed25519 private key (PEM) that signs update manifests")
    secret("PLUGIN_SIGNING_KEY", "release CI: ed25519 private key (PEM) that signs bundled plugins")


def render() -> str:
    body()
    missing = sorted(set(FIELDS) - done)
    if missing:
        raise SystemExit(f"settings fields missing from the generator: {missing}")
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    target = Path(__file__).resolve().parents[1] / ".env.example"
    text = render()
    if "--write" in sys.argv:
        target.write_text(text, encoding="utf-8")
        print(f"wrote {target.name}: {len(text.splitlines())} lines, {len(done)} settings fields")
        return 0
    if target.read_text(encoding="utf-8") != text:
        print(f"{target.name} is stale; run: uv run python scripts/gen_env_example.py --write")
        return 1
    print(f"{target.name} is up to date ({len(done)} settings fields)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
