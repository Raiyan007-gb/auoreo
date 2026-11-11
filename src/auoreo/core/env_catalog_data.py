"""The variable catalog as data, in display order: `core.env_catalog` derives the web's
Settings → Environment entries from it (no `.env` / `.env.example` file is generated or read).

`S` = a `Settings` field (`AUOREO_<NAME>`, stored in state.db), `K` = any other variable
(provider/tool keys stored in secrets.db, pass-through names read from the real environment),
`T` = a free-text note, `Sec` = a section, `Done` = a field covered by an earlier `K`
(e.g. `home` ← `AUOREO_HOME`).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Sec:
    title: str
    id: str
    label: str


@dataclass(frozen=True)
class T:
    text: str


@dataclass(frozen=True)
class S:
    name: str
    note: str = ""
    value: str = ""


@dataclass(frozen=True)
class K:
    env: str
    note: str


@dataclass(frozen=True)
class Done:
    name: str


Item = Sec | T | S | K | Done

HEADER = """# Every variable AUOREO reads. Settings live in state.db and keys in secrets.db, managed from
# the web (Settings → Models / Environment) or `auoreo config` / `auoreo secrets`. A variable set
# in the real process environment (Docker, CI) always wins; [env-only] security knobs and the
# bootstrap fields (home, db_encryption, host, port, workers) are read only from there."""

ITEMS: list[Item] = [
    T(HEADER),
    Sec("models", "models", "Models"),
    T("""# Universal rule: set the key for your provider, then name the model as  <provider>/<model id>
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
# `auoreo doctor` prints one credentials row per configured model and names any missing key."""),
    S("model", "main model, <provider>/<model id>", value="openrouter/anthropic/claude-sonnet-4.5"),
    S("small_model", "cheaper model for titles / compaction / guardian; unset = AUOREO_MODEL"),
    S("embed_model", "embedding model for long-term memory; unset = keyword search only"),
    S("embed_dims", "embedding dimensions (only for models that let you choose)"),
    S(
        "models",
        'named model groups, e.g. {"default":{"deployments":[{"model":"anthropic/claude-sonnet-5"}]}}',
    ),
    S("aliases", 'extra model aliases, e.g. {"fast":"groq/llama-3.3-70b-versatile"}'),
    S("fallbacks", 'per-group fallback chains, e.g. {"default":["openai/gpt-5"]}'),
    S("max_cost_usd", "per-session spend cap in USD (sub-agents count toward the root)"),
    S("auxiliary", "aux model routing per background task (title, compaction, guardian, …)"),
    S("gateway_tuning", "retries / timeouts / cooldowns of the LiteLLM Router"),
    S("i2", "gateway extras: key pools, daily budgets, structured output, OpenRouter lane, deployments"),
    S("bandit", "opt-in model/effort bandit policy (off by default)"),
    Sec("provider keys (fill in only the ones you use)", "providers", "Provider keys"),
    K("OPENROUTER_API_KEY", "OpenRouter — openrouter/<vendor>/<model>"),
    K("ANTHROPIC_API_KEY", "Anthropic — anthropic/<model>"),
    K("OPENAI_API_KEY", "OpenAI — openai/<model>; also OpenAI STT/TTS/Realtime and image generation"),
    K("GEMINI_API_KEY", "Google Gemini — gemini/<model>"),
    K("GROQ_API_KEY", "Groq — groq/<model>; also Groq STT"),
    K("XAI_API_KEY", "xAI — xai/<model>; also xAI STT (experimental)"),
    K("MISTRAL_API_KEY", "Mistral — mistral/<model>; also Mistral STT/TTS"),
    K("DEEPSEEK_API_KEY", "DeepSeek — deepseek/<model>"),
    K("TOGETHERAI_API_KEY", "Together AI — together_ai/<model>"),
    K("FIREWORKS_AI_API_KEY", "Fireworks AI — fireworks_ai/<model>"),
    K("COHERE_API_KEY", "Cohere — cohere/<model>; also the `cohere` memory rerank backend"),
    K("AZURE_API_KEY", "Azure OpenAI — azure/<deployment> (with AZURE_API_BASE + AZURE_API_VERSION)"),
    K("AZURE_API_VERSION", "Azure OpenAI API version, e.g. 2025-04-01-preview"),
    K("AZURE_API_BASE", "Azure OpenAI endpoint, https://<resource>.openai.azure.com"),
    K("OLLAMA_API_BASE", "Ollama server for ollama/<model> (default http://localhost:11434)"),
    Sec("tool and backend keys (only for the features you enable)", "tool_keys", "Tool & backend keys"),
    K("EXA_API_KEY", "web_search: Exa provider"),
    K("PARALLEL_API_KEY", "web_search: Parallel provider"),
    K("BRAVE_API_KEY", "web_search: Brave provider"),
    K("TAVILY_API_KEY", "web_search: Tavily provider"),
    K("JINA_API_KEY", "web_search / web_fetch: Jina search + reader"),
    K("FIRECRAWL_API_KEY", "web_search / web_fetch: Firecrawl provider"),
    K("GH_TOKEN", "gh_* tools: GitHub token (or run `gh auth login` once)"),
    K("GITHUB_TOKEN", "gh_* tools: alternative name for the GitHub token"),
    K("DEEPGRAM_API_KEY", "voice: Deepgram streaming STT"),
    K("DEEPINFRA_API_KEY", "voice: DeepInfra STT"),
    K("ELEVENLABS_API_KEY", "voice: ElevenLabs STT/TTS"),
    K("PICOVOICE_ACCESS_KEY", "voice: Porcupine wake word (PORCUPINE_ACCESS_KEY also accepted)"),
    K("HF_TOKEN", "voice: Hugging Face token for the pyannote diarization models"),
    K("FAL_KEY", "imagegen plugin: fal.ai FLUX provider"),
    K("E2B_API_KEY", "execution environment: E2B sandboxes"),
    K("MODAL_TOKEN_ID", "execution environment: Modal token id"),
    K("MODAL_TOKEN_SECRET", "execution environment: Modal token secret"),
    K("DAYTONA_API_KEY", "execution environment: Daytona sandboxes"),
    K("LIVEKIT_API_KEY", "LiveKit SFU / SIP / agent worker (server URL goes in AUOREO_LIVEKIT)"),
    K("LIVEKIT_API_SECRET", "LiveKit API secret"),
    K("MQTT_PASSWORD", "mqtt plugin: broker password (name configurable via password_secret)"),
    T("# Passed through to the `gh` CLI behind the gh_* tools (standard names; usually unset):"),
    K("GH_HOST", "GitHub Enterprise host for gh_* tools, e.g. github.example.com"),
    K("GH_CONFIG_DIR", "alternative gh config directory"),
    K("HTTPS_PROXY", "outbound proxy for the gh CLI"),
    K("NO_PROXY", "hosts that bypass HTTPS_PROXY"),
    K("SSL_CERT_FILE", "CA bundle for the gh CLI"),
    T("""# Secrets with a `/` in the name cannot be environment variables; store them with
#   auoreo secrets set <name>
#   telegram/bot_token          Telegram connector + delivery
#   discord/bot_token           Discord gateway connector (inbound, approvals)
#   discord/webhook_url         Discord delivery via webhook
#   email/password              email plugin (IMAP/SMTP password; or use `auoreo login google`)
#   calendar/password           calendar plugin (CalDAV)
#   oauth/google_client_secret  Google OAuth client secret (connectors/google/oauth.yaml)
#   webhook/<route>             HMAC secret of webhooks/<route>.yaml (`auoreo webhook add` makes one)
#   provider/<name>/<slot>      extra keys for a provider key pool (i2.key_pools)"""),
    Sec("server", "server", "Server"),
    S("gateway", "litellm (real models) | fake (deterministic, keyless)", value="litellm"),
    S("host", "bind address; anything beyond loopback needs AUOREO_SERVER_PASSWORD"),
    S("port", "HTTP + WebSocket port", value="4096"),
    S("server_password", "required for non-loopback binds; browsers get a short-lived JWT cookie"),
    S("agent", "agent that answers when none is named", value="default"),
    S("workflow", "workflow used for plain chat turns", value="chat"),
    S("cors_origins", "extra allowed browser origins"),
    S("ws_ping_interval_s", "WebSocket keepalive ping"),
    S("instance_name", "name shown to bridged instances"),
    S("locale", "UI / prompt locale, e.g. en-US"),
    S("workers", "uvicorn workers; >1 requires AUOREO_REDIS_URL"),
    S("redis_url", "Redis for multi-worker event fan-out, redis://host:6379/0"),
    S("mcp_server_enabled", "serve AUOREO itself as an MCP server at /mcp"),
    S("http", "Host allowlist, trusted proxies, cookie + queue limits"),
    S("cli", "serve flags: ws, tunnel, trusted hosts, mDNS, ws_auth (token|jwt|capability|off)"),
    S("cli2", "loop / heartbeat / worktree gc / debug upload endpoint"),
    S("h1b", "JWT session cookies, capability tokens, keyring policy on public binds"),
    S("i5", "web UI/CLI extras: share links, skills hub UI, doctor --fix, stream-json guard"),
    S("env", "custom variables from Settings → Environment (secret values live in secrets.db)"),
    Sec("security (real environment only)", "security", "Security (read-only)"),
    T("""# These are frozen from the real process environment at startup so prompt-injected repos can't
# loosen them. Set them with `export` (or your service manager), never in a .env file."""),
    S("permission_mode", "manual | smart | yolo | plan | acceptEdits (yolo also needs AUOREO_YOLO=1)"),
    K("AUOREO_YOLO", "set to 1 to allow permission_mode=yolo (floors still apply)"),
    S("smart_timeout", "smart mode: guardian review budget in seconds (else i9.guardian.timeout_s, 15)"),
    T("""# approvals.smart_policy: there is no separate knob — the guardian policy text is the
# `prompts/guardian.md` section (role: guardian); override it in ~/.auoreo/prompts/ and keep
# approvals.unattended (approve|deny per single_query/cron/platform) for no-human runs."""),
    S("approvals", "unattended approval policy per run kind: single_query / cron / platform"),
    S("approval_timeout_s", "how long an approval card waits for you"),
    S("security", "redaction modes, sensitive writes, sandbox Landlock layer, trust settings"),
    S("floors", "hardline-floor knobs (oversized inline command threshold, …)"),
    S("redact_secrets", "mask secrets in logs, events and tool output"),
    K("AUOREO_REDACT_PHONES", "set to 1 to also mask phone numbers"),
    S("keyring", "require | allow-file | forbid-file — where the secrets-encryption key may live"),
    S("backends", 'backend per kind, e.g. {"sandbox":"bwrap","stt":"openai"}; sandbox default auto'),
    S("sandbox_policy", "warn | fail when the selected sandbox cannot start"),
    S("sandbox_docker_image", "image for the docker sandbox backend"),
    S("sandbox_docker_mem_limit", "memory limit for the docker sandbox backend"),
    S("allow_private_urls", "let web tools reach private / loopback addresses (SSRF floor off)"),
    S("webhooks", "webhook ingress: strict_secrets (default true), size limits"),
    S("extra_roots", "extra definition roots (agents/tools/skills …) after the bundled one"),
    K("AUOREO_HOME", "runtime home, default ~/.auoreo (Windows %LOCALAPPDATA%\\auoreo)"),
    K("AUOREO_PROFILE", "profile name: uses <home>/profiles/<name>/ (same as --profile)"),
    Done("home"),
    Sec("database and secrets at rest", "database", "Database"),
    S("db_encryption", "off | columns | sqlcipher"),
    S("db_query_dsns", 'named DSNs for the db_query tool, values as secret refs: {"prod":"secret:prod_dsn"}'),
    Sec("sessions, compaction, memory", "sessions", "Sessions, compaction & memory"),
    S("loop", "agent-loop limits (steps, budgets, doom-loop guards)"),
    S("compaction", "compaction thresholds, idle compaction, memory nudges, query rewrite"),
    S("i4", "compaction extras, memory scopes/tenants/decay, re-embed, CJK search"),
    S("memory", "long-term memory: embed model, recall, HyDE, graph, chunking"),
    S("rerank_model", "local cross-encoder for memory rerank (backends.rerank)"),
    S("rerank_download_models", "allow downloading rerank weights"),
    S("rerank_timeout_s", ""),
    S("rerank_top_n", ""),
    S("strict_overlays", "fail (not warn) when a user overlay shadows a bundled definition"),
    S("plan_requires_verify", "exit_plan_mode requires a verify_plan PASS first"),
    S("suggestions_enabled", "consent-first proactive suggestions"),
    S("suggestions_home", "delivery target for suggestions (backends/delivery/<name>)"),
    S("suggestions_quiet_hours", "e.g. 22:00-07:00"),
    S("suggestions_max_pending", ""),
    Sec("tools, shell, search, LSP", "tools", "Tools, search & LSP"),
    S("tools", "tool knobs: PTY/jobs limits, apply_patch LSP diagnostics, doc caps, pandas"),
    S("tool_engine", "check_fn TTL, collision policy, spill cap, MCP sampling limits"),
    S("h5", "apply_patch formatter step, run_code / video_analyze limits, metric store"),
    S("i9", "egress proxy (allow/deny hosts), search provider order + cache, guardian timeout"),
    S("m7", "M7 feature flags (all off): token economy, SSH hosts, agent routing, worktrees, merge-back"),
    S("searxng_url", "self-hosted SearXNG for web_search, http://localhost:8888"),
    S("shell_max_processes", "concurrent persistent shells per session"),
    S("lsp_timeout_ms", ""),
    S("lsp_servers", 'language-server commands, e.g. {"python":["uvx","ty","server"]}'),
    S("browser_executable_path", "Chromium/Chrome binary for the browser backend"),
    S("github_allowed_repos", 'repos the gh_* tools may touch, e.g. ["owner/repo"]'),
    Sec("automation, teams, plugins, MCP", "automation", "Automation, teams & plugins"),
    S("hooks_enabled", ""),
    S("scheduler_enabled", "run jobs/*.yaml on their schedules"),
    S("webhooks_enabled", "accept POST /hooks/<route>"),
    S("automation_workers", ""),
    S("automation_start_delay_s", ""),
    S("automation_poll_s", ""),
    S("automation_run_timeout_s", ""),
    S("automation_deliveries_keep", "terminal delivery rows kept (0 = all)"),
    S("teams_max_depth", "delegation depth limit"),
    S("teams_max_children", "concurrent children per parent"),
    S("teams_child_timeout_s", ""),
    S("teams_orchestrator_enabled", "opt-in supervisor that can spawn at depth 2"),
    S("teams2", "delegation pause, heartbeat/stale timings, job timeout, chat approvals"),
    S("i3", "kanban dispatcher (off by default), cron extras, delegation naming"),
    S("plugins_enabled", ""),
    S("plugins", 'per-plugin config, e.g. {"telegram":{"enabled":true,"owners":["123"]}}'),
    S("plugins_python", "Python for plugin venvs"),
    S("plugins_seed", "copy bundled plugins into ~/.auoreo/plugins on first start"),
    S("plugins_poll_s", ""),
    S("plugins_tool_timeout_s", ""),
    S("plugins_backoff_scale", ""),
    S("i6", "plugin trust tiers/taps/allow-list, MCP roots + sampling, GitHub /meta CIDRs"),
    S("g6", "voice extras, PREFS proposals, skills hub taps, review worktrees"),
    Sec("voice and WebRTC", "voice", "Voice & WebRTC"),
    S("voice_enabled", ""),
    S("voice_download_models", "allow downloading local STT/TTS/wake models"),
    S("voice_language", "STT language hint, e.g. en"),
    S("voice_stt_model", "STT model id for the selected stt backend"),
    S("voice_tts_voice", "TTS voice id for the selected tts backend"),
    S("voice_speaking_rms", ""),
    S("voice_endpoint_silence_ms", "silence that ends an utterance"),
    S("voice_wake_phrase", ""),
    S("voice_kws_model_dir", "keyword-spotting model folder (sherpa-onnx)"),
    S("voice_speak_over_rtc", ""),
    S("voice_speak_max_chars", ""),
    S("voice_barge_in_interrupts_turn", ""),
    S("voice_echo_margin", ""),
    S("voice_echo_corr", ""),
    S("voice_meeting_keep_audio", ""),
    S("voice_meeting_ingest", ""),
    S("voice_diarize_segmentation_model", ""),
    S("voice_diarize_embedding_model", ""),
    S("voice_diarize_num_speakers", ""),
    S("voice_diarize_threshold", ""),
    S("voice_host_python", "Python for the out-of-process voice sidecar"),
    S("voice_host_timeout_s", ""),
    S("voice_host_backends", "backends the voice sidecar runs (stt/tts/wake)"),
    S("tone_enabled", ""),
    S("tone_adaptive", ""),
    S("i8", "wake daemon, STT filters/keyterms, LiveKit worker, computer-use safety, vision"),
    S("rtc_max_peers", ""),
    S("rtc_ice_servers", 'STUN/TURN URLs, e.g. ["stun:stun.l.google.com:19302"]'),
    S("rtc_audio_level_interval_s", ""),
    S("rtc_speaking_rms", ""),
    S("turn_urls", 'your TURN server URLs, e.g. ["turns:turn.example.com:443"]'),
    S("turn_secret_ref", "secret name holding the TURN shared secret (auoreo secrets set …)"),
    S("turn_ttl_s", ""),
    S("livekit", 'LiveKit server, e.g. {"url":"wss://lk.example.com"} (keys: LIVEKIT_API_KEY/SECRET)'),
    S("teleop_input_enabled", "allow remote keyboard/mouse input over WebRTC"),
    Sec("computer use and perception", "perception", "Computer use & perception"),
    S("cua_driver_command", "desktop driver binary"),
    S("cua_telemetry", ""),
    S("detection_model", "object detection size: nano | small | medium | base | large"),
    S("perception_download_models", "allow downloading detection/face models"),
    S("faces_enabled", "face detection (off by default; embeddings never stored)"),
    S("face_nc_opt_in", "opt in to non-commercial face models"),
    S("yolo_sidecar_url", "AGPL-isolated YOLO sidecar, http://127.0.0.1:<port>"),
    S("perception_sample_rtc_video", ""),
    S("perception_rtc_interval_s", ""),
    Sec("execution environments and remote plane", "environments", "Environments & remote"),
    S("environments", 'named environments, e.g. {"gpu":{"backend":"ssh","host":"gpu.local"}}'),
    S("environment_docker_image", ""),
    S("environment_docker_network", ""),
    S("environment_docker_mem_limit", ""),
    S("environment_ssh", '{"host":"…","user":"…","key_secret":"ssh_key"}'),
    S("environment_e2b_template", ""),
    S("environment_modal_app", ""),
    S("environment_modal_image", ""),
    S("environment_daytona_api_url", ""),
    S("environment_daytona_snapshot", ""),
    S("environment_sandbox_timeout_s", ""),
    S("environment_remote", '{"url":"ws://host:port","token_secret":"exec_token"} for an exec-server'),
    S("bridges", "named peer instances for the signed instance bridge"),
    K("AUOREO_URL", "CLI: talk to this running server instead of working in-process"),
    K("AUOREO_EXEC_TOKEN", "`auoreo exec-server` bearer token (prefer --token-secret)"),
    Sec("observability", "observability", "Observability"),
    S("logging", "log format text | json, level"),
    S("otel_mode", "off | local | otlp"),
    S("otel_capture_content", "include prompts/outputs in spans (off: metadata only)"),
    K("OTEL_EXPORTER_OTLP_ENDPOINT", "OTLP collector for otel_mode=otlp, http://localhost:4318"),
    K("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "traces-only OTLP endpoint (overrides the one above)"),
    K("OTEL_EXPORTER_OTLP_HEADERS", "OTLP auth headers, key=value,key2=value2"),
    K("OTEL_TRACES_SAMPLER_ARG", "trace sampling ratio 0..1"),
    S("sentry_dsn", "Sentry DSN (needs the sentry extra)"),
    S("sentry_share", "full | errors_only | off"),
    S("trace_file", "write a Perfetto/Chrome trace JSON here"),
    Sec("updates", "updates", "Updates"),
    S("update_channel", "stable | beta | dev"),
    S("update_url", "signed update manifest URL template"),
    S("update_pubkeys", "extra ed25519 public keys trusted for update manifests"),
    Sec("developer and CI (leave blank for normal use)", "developer", "Developer"),
    K("AUOREO_OFFLINE", "set to 1 to skip network probes in doctor and backends"),
    K("AUOREO_CODE_SHA", "override the code hash reported by /api/health"),
    K("AUOREO_ALLOW_NO_WEB_DIST", "set to 1 to build a wheel without web/dist"),
    K("AUOREO_TEST_PROVIDER_BASE", "tests only (fake gateway): base URL for provider model-list calls"),
    K("UPDATE_SIGNING_KEY", "release CI: ed25519 private key (PEM) that signs update manifests"),
    K("PLUGIN_SIGNING_KEY", "release CI: ed25519 private key (PEM) that signs bundled plugins"),
]
