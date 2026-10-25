# AUOREO

A **filesystem-native agent harness**. Agents, tools, skills, workflows, prompts,
permission rules and MCP servers are folders; the Python core discovers, validates and
runs them at runtime. Add a capability by adding a directory — no orchestration code.

```
agents/default/AGENT.md        # an agent = frontmatter + instructions
tools/read/tool.yaml           # a tool = JSON-Schema parameters + permission metadata
tools/read/handler.py          #          + async def run(args, ctx)
skills/code-review/SKILL.md    # lazy-loaded instructions (Agent Skills format)
workflows/chat.yaml            # agent chains
prompts/*.md                   # system-prompt sections (ordered, strict {{vars}})
permissions/defaults.yaml      # last-match-wins rules + unbypassable floors
mcp/<server>.yaml              # MCP servers → mcp__<server>__<tool> tools
commands/<name>.md             # slash commands (/compact, /model, /plan, /review)
```

Overlays: put the same folders under `~/.auoreo/` or `<project>/.auoreo/` to add or
override definitions (runtime > project > bundled). `POST /api/harness/reload` or a
restart picks them up.

## Quick start

```bash
uv sync
uv run auoreo doctor
uv run auoreo harness list

# keyless demo with the deterministic fake gateway
AUOREO_GATEWAY=fake uv run auoreo run '/tool read {"path": "README.md"}'

# real models via OpenRouter (LiteLLM Router)
uv run auoreo secrets set OPENROUTER_API_KEY
uv run auoreo run "summarize this repo"

# the API server + web UI on one port (SSE at /api/events, WebSocket at /ws)
(cd web && npm ci && npm run build)
uv run auoreo web
```

Long sessions are compacted automatically (pressure or context overflow) and on `/compact`;
replaced messages stay searchable in the archive. `auoreo stats` shows token/cost usage;
`auoreo tunnel start --provider cloudflared` exposes the server (password required).

## API (one port, default 4096)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness + harness generation |
| POST | `/api/chat` | queue a message (`Idempotency-Key` supported) → 202 |
| GET | `/api/events` | SSE stream: replay from `Last-Event-ID`, then live tail |
| GET/POST | `/api/sessions[/{id}[/messages|/cancel]]` | sessions |
| GET | `/api/permissions/pending` · POST `/api/permissions/{id}/reply` | approvals (`once|always|reject`) |
| GET | `/api/harness[/{kind}]` · POST `/api/harness/reload` | discovered definitions |
| GET | `/api/memory/search` · POST `/api/memory/facts` | long-term memory |
| POST | `/api/sessions/{id}/fork|archive|unarchive|compact` · GET `…/export?format=json|md` · GET `…/archive` | session operations |
| GET | `/api/usage?session_id=` · `/api/usage/summary?days=` | token/cost ledger |
| GET | `/api/commands` | slash commands from `commands/` |
| WS | `/ws?last_seq=` | control plane: replay + live events, approvals, questions, steer, interrupt |

See `AGENTS.md` for architecture and contributor workflow, `spec.md` for the full roadmap.
