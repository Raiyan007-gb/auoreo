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
| `memory/*.md` | Templates copied into `~/.auoreo/memory/` |
| `src/auoreo/harness/` | discovery → loader → registry → orchestrator |
| `src/auoreo/runtime/` | inbox, agent loop, session coordinator |
| `src/auoreo/gateway/` | LLM gateway (LiteLLM Router) + fake gateway |
| `src/auoreo/core/` | settings, DB, events, bus, audit, redaction, permissions, approvals, secrets |
| `src/auoreo/tools/` | tool *engine* only (executor, context, net helpers) — no tool bodies |
| `src/auoreo/memory/` | FTS5 + sqlite-vec hybrid memory |
| `src/auoreo/api/`, `src/auoreo/cli/` | FastAPI app (one port) and Typer CLI |

Definition roots, highest precedence first: `~/.auoreo/` → `<cwd>/.auoreo/` → repo root
(bundled) → `extra_roots` from `settings.json`.

## Invariants

- Adding an agent/tool/skill/workflow/prompt section is a **new folder**, never engine code.
- One FastAPI app, one port (default 4096). The `events` table is append-only and the
  source of truth; the bus only fans out.
- `litellm` is imported only in `src/auoreo/gateway/gateway.py` (test-enforced).
- Pydantic models in `src/auoreo/schema/` are the wire contract → `web/sdk/openapi.json`.
  Run `uv run python scripts/check_openapi.py` after API changes.
- Secrets come only from env / keyring / `secrets.db`. Tools never read `.env` or
  `secrets.db` (executor-enforced floor) and child processes get a scrubbed env.
- No `elif` chains of 4+ (use dict dispatch); files ≤ 500 lines (`api/app.py` ≤ 300).

## Workflow

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
uv run auoreo harness validate
uv run python scripts/check_openapi.py --check
AUOREO_GATEWAY=fake uv run auoreo serve --port 0     # keyless live run
```

Use Context7 (`resolve-library-id` → `query-docs`) before writing code against a
versioned dependency, and note the library ID in the commit body.

## Spec drift (deliberate)

1. One package `src/auoreo/` with subpackages instead of 10 `agent_*` workspace packages.
2. MCP Python SDK **v2** (`mcp.server.MCPServer`, `mcp.Client`) instead of `mcp<2`.
3. Dependency floors re-resolved to Oct 2026 (FastAPI 0.162, SQLAlchemy 2.1, LiteLLM 1.106,
   Pydantic 2.14, Typer 0.27, MCP 2.3).
4. Memory lives in `~/.auoreo/memory.db` (sync sqlite3 + sqlite-vec via worker threads),
   separate from `state.db`, so extension loading never touches the SQLAlchemy pool.
5. Schema is created with `create_all` + a `schema_version` refuse-to-start guard;
   Alembic migrations arrive with the first schema change (spec M3).
6. Event `id`/`seq` is a global autoincrement (needed for one resumable stream across
   sessions); `aggregate_seq` carries the per-session sequence.
