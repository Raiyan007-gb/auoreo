# AGENTS.md — working on AUOREO

AUOREO is a **filesystem-native agent harness**: the Python core (`src/auoreo/`) is a
generic engine that discovers, validates and runs capabilities defined as folders.
`spec.md` is the product spec (written as "OREO"; this repo names it `auoreo`).

## Layout

| Path | What lives there |
|---|---|
| `agents/<name>/AGENT.md` | Agent: frontmatter config + prompt body |
| `tools/<name>/tool.yaml` + `handler.py` | Tool: JSON-Schema params + async handler |
| `skills/<name>/SKILL.md` | Agent Skills (lazy-loaded instructions) |
| `workflows/<name>.yaml` | Agent chains |
| `prompts/*.md`, `prompts/families/*.md`, `prompts/variants.yaml` | System prompt sections, model-family overlays, request shaping |
| `permissions/*.yaml` | Permission rules + unbypassable floors |
| `mcp/<name>.yaml` | MCP servers (tools become `mcp__<server>__<tool>`) |
| `backends/<kind>/<name>/backend.yaml` + `impl.py` | Device/library backends (capture, browser, stt, search, audio_input, …) behind ABCs in `src/auoreo/backends/`; selected via `settings.backends` |
| `hooks/<name>.yaml` | Lifecycle hooks (Pre/PostToolUse, Stop, Session*, *Compact): a command gets the event as JSON on stdin |
| `jobs/<name>.yaml` | Scheduled jobs (cron / `every` / one-shot); APScheduler fires, AUOREO runs the session and delivers |
| `webhooks/<route>.yaml` | `POST /hooks/<route>`: HMAC-verified ingress → agent session → delivery |
| `plugins/<name>/plugin.yaml` + `pyproject.toml` | Out-of-process plugins (own venv, JSON-RPC over stdio): tools, delivery providers, connectors |
| `policies/<name>.star` | Starlark exec policies: `# key: value` header + `decide(req)`; run after floors, before rules |
| `commands/<name>.md` | Slash commands (`/compact`, `/model`, `/plan`, `/review`); `handler: prompt` renders the body |
| `memory/*.md` | Templates copied into `~/.auoreo/memory/` |
| `src/auoreo/core/env_catalog*.py`, `settings_store.py`, `env_store.py`, `live_settings.py`, `env_*.py` | The variable catalog (feeds Settings → Environment only), the settings store in `state.db` (`app_settings` + history + version counter; pydantic source in `settings.py`; one-time `settings.json` migration), live apply (in-place `svc.settings`, per-area refresh, DB-version + secrets.db watcher), custom variables, the one-time `.env` import; API `api/routers/env_api.py` |
| `src/auoreo/gateway/providers.py`, `provider_fetch.py`, `core/provider*.py`, `core/model_selection.py` | Settings → Models: provider registry + key detection, list-models clients/parsers (httpx, no litellm), `provider_connections`/`provider_models` (keys only in secrets.db), model selection → settings; API `api/routers/providers.py`, wire `schema/providers.py` |
| `src/auoreo/harness/` | discovery → loader → registry → orchestrator |
| `src/auoreo/runtime/` | inbox, agent loop, session coordinator, compaction, slash-command dispatch, titles |
| `src/auoreo/gateway/` | LLM gateway (LiteLLM Router) + fake gateway |
| `src/auoreo/core/` | settings, DB + Alembic migrations, events, bus, audit, redaction, permissions, approvals, questions, secrets, usage ledger, tunnels, WS connections, `extensions.py` (starter seam for optional subsystems) |
| `src/auoreo/automation/`, `src/auoreo/teams/`, `src/auoreo/plugins/`, `src/auoreo/rtc/`, `src/auoreo/voice/` | Optional subsystems attached through `core/extensions.py` (webhooks + scheduler + deliveries; delegation/mailbox/kanban; plugin host; WebRTC; voice service + RTC bridge). The sandbox probe and memory curator are starters too |
| `src/auoreo/tools/` | tool *engine* only (executor, stages, egress proxy + `net` stage, locks, context, net helpers) — no tool bodies |
| `src/auoreo/memory/` | FTS5 + sqlite-vec hybrid memory |
| `src/auoreo/api/`, `src/auoreo/cli/` | FastAPI app (one port; REST + SSE `/api/events` + WS `/ws`) and Typer CLI |
| `web/` | React SPA (`web/app`), typed SDK (`web/sdk`, generated from `openapi.json`), Playwright e2e; `web/dist` is served at `/` |
| `sdk/python/` | Generated Python client (`scripts/gen_sdk.sh`, re-run after API changes) + hand-written SSE extras |
| `src/auoreo/policy/`, `src/auoreo/remote/`, `src/auoreo/perception/`, `src/auoreo/goals/`, `src/auoreo/suggestions/`, `src/auoreo/skillhub/`, `src/auoreo/mcp/` | Bandit model/effort policy; daemon + exec-server + bridge; sensor sessions; goals; consent-first suggestions; skills hub (taps, lockfile, quarantine); MCP bridge + sampling |
| `packaging/`, `Dockerfile`, `compose.yaml`, `packages/npm/`, `desktop/` | Wheel build hook, PyInstaller spec, update-manifest signing; container; npm launcher; Electron shell |
| `docs/` | `roadmap-m3-plus.md`, `compat.md` (`/v1`, `/mcp`, `/acp`), `voice.md`, `desktop.md`, `environments.md`, `environment.md` (web-managed env), `remote.md`, `perception.md`, `teleop.md`, `release.md` |

Definition roots, highest precedence first: `~/.auoreo/` → `<cwd>/.auoreo/` → repo root
(bundled) → `extra_roots` from the stored settings.

## Invariants

- Adding an agent/tool/skill/workflow/prompt section is a **new folder**, never engine code.
- One FastAPI app, one port (default 4096). The `events` table is append-only and the
  source of truth; the bus only fans out.
- `litellm` is imported only in `src/auoreo/gateway/gateway.py` (test-enforced).
- Pydantic models in `src/auoreo/schema/` are the wire contract → `web/sdk/openapi.json` →
  `web/sdk/types.gen.ts`. Run `uv run python scripts/check_openapi.py` then
  `cd web && npm run gen:types` and `bash scripts/gen_sdk.sh` after API changes; WS frames live in `schema/ws.py` and reuse
  the SSE `ServerEvent` envelope (never a second event model).
- A new optional subsystem registers one `module:function` line in `core/extensions.STARTERS`;
  it never edits `services.py`. Device/library integrations are `backends/` with a fake.
- Schema changes need an Alembic revision in `src/auoreo/core/alembic/versions/` and a
  `SCHEMA_VERSION` bump; `Database.init()` upgrades file databases in place.
- Hardline floors (`core/floors.py`, `floors_secrets.py`) run before policies, rules and yolo; `[env-only]` security knobs (`core/env_rules.py`) come only from the real process environment.
- No `.env`: nothing loads one at startup and there is no `.env.example`. Settings live in `state.db`
  (`app_settings`), keys in `secrets.db`, provider connections in `state.db` (key names only), all
  managed from the web (Settings → Models / Environment) or `auoreo config` / `auoreo secrets`. The
  real process environment still wins (Docker/CI); bootstrap fields (`home`, `db_encryption`, `host`,
  `port`, `workers`, plus `AUOREO_PROFILE`) are env/CLI-only. The only `.env` reader is the
  user-confirmed web import (`core/env_import.py`); `auoreo doctor` warns about a leftover file.
- A project `<cwd>/.auoreo/` is inert (no hooks, MCP, tool code, plugins, policies or allow rules) until trusted (`auoreo trust`); tools reach the network only through the egress proxy unless a one-shot `net_direct` grant with a justification says otherwise.
- Secrets come only from env / keyring / `secrets.db`. Tools never read `.env` or
  `secrets.db` (executor-enforced floor) and child processes get a scrubbed env.
- Web-managed environment (`/api/env`, `docs/environment.md`): secrets are write-only (no response,
  event or audit row carries a value), the real environment always wins (409), `[env-only]` knobs
  are read-only (403), the web never writes a `.env`. New variables go in
  `core/env_catalog_data.py` (the catalog only feeds the web); a startup-only field goes in
  `env_catalog.RESTART_REQUIRED`, a cached one gets a refresh path in `core/live_handlers.py`.
- No `elif` chains of 4+ (use dict dispatch); files ≤ 500 lines (`api/app.py` ≤ 300).
- Every path must check out on Windows: no reserved names (`con`, `prn`, `aux`, `nul`, `com1`…,
  `lpt1`… — even with an extension), no `<>:"|?*`, no case-only clashes, no symlinks
  (`tests/unit/test_portable_paths.py`).

## Workflow

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
uv run auoreo harness validate
uv run python scripts/check_openapi.py --check
uv run python scripts/verify_tool_catalog.py --check   # --write after adding/changing a tool
./scripts/run_tests.sh [--per-file]                     # hermetic: env -i, fresh HOME
cd web && npm ci && npm run typecheck && npm run lint && npm run build && npm run e2e
cd web && npm run e2e:visual                            # screenshot comparison (web/README.md "Quality gates")
AUOREO_GATEWAY=fake uv run auoreo web --port 0       # keyless live run with the UI
```

Use Context7 (`resolve-library-id` → `query-docs`) before writing code against a
versioned dependency, and note it in the commit body as one trailer per source:
`Docs: <URL> | <libraryID>@<version> | accessed YYYY-MM-DD`. PRs fill the evidence table in
`.github/pull_request_template.md`. Test tiers: gateway HTTP replays VCR cassettes
(`tests/fixtures/cassettes/<provider>/<case>.yaml`, re-record with `VCR_RECORD=1`); session
replays compare snapshots (`tests/fixtures/snapshots/`, refresh with `AUOREO_SNAPSHOT=update`,
never in CI). Tools declare `net: none | proxy | direct` (I9 egress: `proxy` goes through the
loopback egress proxy, `direct` needs a `net_justification` + approval).

## Spec drift (deliberate)

1. One package `src/auoreo/` with subpackages instead of 10 `agent_*` workspace packages.
2. MCP Python SDK **v2** (`mcp.server.MCPServer`, `mcp.Client`) instead of `mcp<2`.
3. Dependency floors re-resolved to Oct 2026 (FastAPI 0.162, SQLAlchemy 2.1, LiteLLM 1.106,
   Pydantic 2.14, Typer 0.27, MCP 2.3).
4. Memory lives in `~/.auoreo/memory.db` (sync sqlite3 + sqlite-vec via worker threads),
   separate from `state.db`, so extension loading never touches the SQLAlchemy pool.
5. File databases are migrated by Alembic at startup (`0001_baseline` = M2 schema,
   `0002_schema_v2`); `:memory:` test databases use `create_all`. A `schema_version` newer
   than the code refuses to start.
6. Event `id`/`seq` is a global autoincrement (needed for one resumable stream across
   sessions); `aggregate_seq` carries the per-session sequence.
7. A wrong WebSocket token is rejected at the handshake (HTTP 403) rather than with a 1008
   close, because the check runs before `accept()`.
8. Titles are generated by one aux call after the first turn; tests that inspect
   `gateway.requests` filter `metadata.purpose in {"title", "compaction"}`.
9. `serve --ws-auth` accepts `token|jwt|capability|off` and defaults to `token` (the server
   password or a session JWT); the spec enum is `off|capability|jwt`. `token` is a deliberate
   extension so a password-only setup works without minting JWTs; `off` stays loopback-only.
