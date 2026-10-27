# Compat surfaces (M5h)

AUOREO serves three protocols besides its own `/api` + `/ws`, all on the same port
(default 4096), behind the same auth:

| Surface | Path | What it is |
|---|---|---|
| OpenAI-compatible | `GET /v1/models`, `POST /v1/chat/completions` | Thin translate → gateway (no agent loop) |
| MCP server | `POST/GET/DELETE /mcp` | The agent's tools, read-only resources and slash-command prompts for external MCP clients (streamable HTTP) |
| ACP | `POST /acp` | Agent Client Protocol (JSON-RPC 2.0) for editors |

Plus the generated **Python SDK** in `sdk/python/` (see its README).

## Auth

When `AUOREO_SERVER_PASSWORD` is set, every surface needs `Authorization: Bearer <password>`
(or the `auoreo_token` cookie). Without a password the server is open (and logs a warning).
`/mcp` also checks the browser `Origin`: it must be loopback or listed in `cors_origins`,
otherwise 403 (non-browser clients send no Origin). Wrong/missing token → 401.

## OpenAI-compatible `/v1`

`POST /v1/chat/completions` takes `{model, messages, tools?, tool_choice?, temperature?,
max_tokens?|max_completion_tokens?, stream?, stream_options?, user?}` (other OpenAI fields are
accepted and ignored), builds an `LLMRequest` and calls the gateway directly.

- **Models**: `GET /v1/models` lists the routing groups in `settings.models`, the same
  groups as `auoreo/<group>`, and `settings.aliases`. Any other string is passed to the
  gateway as-is (e.g. a LiteLLM model id).
- **Shaping**: `prompts/variants.yaml` applies as in the agent loop: the first matching
  variant's `omit_fields` are dropped and its `temperature` is the default when the
  request has none.
- **Tools are the client's.** Tool calls come back in OpenAI shape (`choices[0].message.tool_calls`,
  or `delta.tool_calls` with `index/id/function.name/arguments` when streaming); the client
  runs them and sends `role: "tool"` messages. Nothing executes server-side, so no
  permission prompt is involved. `tool_choice: "none"` is honoured; `"required"` or a named
  function degrade to `"auto"`.
- **Streaming**: `stream: true` returns SSE `data: {chat.completion.chunk}` lines: the
  `role` first, then `content` deltas, tool calls, a chunk with `finish_reason`, a final
  `usage` chunk (`choices: []`) when `stream_options.include_usage`, then `data: [DONE]`.
- **Errors** use the OpenAI envelope `{"error": {"message", "type", "code"}}`. Gateway codes
  map AUTH → 401, RATE_LIMIT/QUOTA → 429, CONTEXT/INVALID_REQUEST/CONTENT_POLICY → 400,
  PAYLOAD_TOO_LARGE → 413, MODEL_NOT_FOUND → 404, BUDGET/BILLING → 402, anything else → 502.
  An error after streaming started is sent in-band as one `data: {"error": …}` line before
  `[DONE]`. Request validation errors are FastAPI 422s.
- **Accounting**: each call writes a usage row (session `compat`, run id = completion id)
  and an audit row `compat.openai` (model, token counts, finish reason, message/tool counts;
  never message bodies). Response text is redacted like any model output.

```bash
curl -N http://127.0.0.1:4096/v1/chat/completions \
  -H "Authorization: Bearer $AUOREO_SERVER_PASSWORD" -H 'Content-Type: application/json' \
  -d '{"model":"default","stream":true,"stream_options":{"include_usage":true},
       "messages":[{"role":"user","content":"hello"}]}'
# data: {"id":"chatcmpl-…","object":"chat.completion.chunk",…,"choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}],"usage":null}
# data: {…"delta":{"content":"echo: hello"}…}
# data: {…"delta":{},"finish_reason":"stop"…}
# data: {…"choices":[],"usage":{"prompt_tokens":…,"completion_tokens":…,"total_tokens":…}}
# data: [DONE]
```

The official `openai` Python client works with `base_url="http://127.0.0.1:4096/v1"` and
`api_key=<server password>` (any string when no password is set).

## MCP server `/mcp`

Streamable HTTP via the MCP Python SDK v2: a low-level `mcp.server.Server` with
`on_list_tools` / `on_call_tool` handlers (the tools are schema-first, so the JSON Schema
from each `tool.yaml` is served as-is) behind the SDK's `StreamableHTTPSessionManager`,
which handles `Mcp-Session-Id` (issued on `initialize`, 400/404 on a missing/unknown id,
`DELETE` to end), 202 for notifications and the optional GET SSE stream. It is added to
the FastAPI app through the `MOUNTS` hook in `api/app.py` (before the SPA mount); the app's
lifespan runs the session manager. `MCP-Protocol-Version` is echoed. Disable with
`auoreo config set mcp_server_enabled false` (stored in state.db).

- **Tools**: the `direct` tools of `settings.agent` (default `default`), minus
  `interactive_only` ones, rebuilt from the live harness on each request (hot reload shows
  up without a restart).
- **Execution**: every call runs through the same `ToolExecutor` as the agent loop with a
  `PermissionEngine` from the agent's rules and the unbypassable floors — schema
  validation, hooks, permissions, redaction, result pruning and the audit log all apply.
  The tool session id is `mcp-<Mcp-Session-Id>` (a random id for sessionless calls).
- **Non-interactive**: there is no human to ask, so anything that resolves to `ask` (e.g.
  `bash`, `write` in `manual` mode) is **denied** with an explanatory tool error. Allow it
  with a permission rule if you want external clients to use it.

```python
import asyncio, httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main() -> None:
    async with httpx2.AsyncClient(headers={"Authorization": "Bearer <password>"}) as http:
        async with Client(streamable_http_client("http://127.0.0.1:4096/mcp", http_client=http)) as c:
            print([t.name for t in (await c.list_tools()).tools])
            print((await c.call_tool("read", {"path": "/abs/path/README.md"})).content[0].text)


asyncio.run(main())
```

Without a password, `Client("http://127.0.0.1:4096/mcp")` is enough.

### Resources and prompts (F3)

Read-only resources (`api/mcp_resources.py`). Every read is redacted (`core.redaction`),
capped at **256 KiB** (longer content is cut at a code-point boundary and ends with a
`> [truncated by AUOREO: …]` notice) and audited as `mcp.resource.read` (`detail.uri`,
bytes, truncated; `decision=deny` for unknown URIs, which fail with `-32602`).

| URI | Content | MIME |
|---|---|---|
| `auoreo://memory/<FILE>.md` | `MEMORY.md`, `USER.md`, `PREFS.md`, `PROJECT.md` under `<home>/memory/` | `text/markdown` |
| `auoreo://skills/<name>` | the skill's `SKILL.md` | `text/markdown` |
| `auoreo://harness/<kind>` | `harness.describe()[kind]` (tools, agents, skills, workflows, mcp, prompts …) | `application/json` |
| `auoreo://sessions/<id>` | the markdown transcript (`GET /api/sessions/{id}/export?format=md`) | `text/markdown` |

`resources/list` shows the existing memory files, every skill and harness kind and the 50
newest sessions; `resources/templates/list` gives the four parameterised forms. A token
sees every session today, so every session id is readable.

**Prompts**: each `commands/<name>.md` with `handler: prompt` is an MCP prompt with one
optional `args` argument; `prompts/get` renders the body with the runtime's strict
`{{args}}` / `{{input}}` renderer and returns it as one user message. Engine-handled
commands (`/compact`, `/model`, `/plan`) are not exposed.

**Change notifications**: on 2026-07-28 sessions, `subscriptions/listen` (the SDK's
`ListenHandler`) delivers `ResourceUpdated` for memory files and `resources`/`prompts`
list-changed after a harness reload; handshake-era sessions can `resources/subscribe` to a
memory URI and get `notifications/resources/updated`. Both are driven by a 30 s
mtime/generation poll that runs only while at least one subscriber exists. Capabilities
advertise `resources` (with `subscribe`) and `prompts`; `listChanged` is advertised on
2026-07-28 sessions (where it is delivered over `subscriptions/listen`).

```python
async with Client("http://127.0.0.1:4096/mcp") as c:
    print([str(r.uri) for r in (await c.list_resources()).resources])
    print((await c.read_resource("auoreo://memory/MEMORY.md")).contents[0].text)
    print((await c.get_prompt("review", {"args": "src/app.py"})).messages[0].content.text)
```

## ACP `/acp`

JSON-RPC 2.0 over `POST /acp`, single messages or batches of up to 32 (processed
concurrently, answered in order). camelCase wire names, absolute paths, 1-based lines.

| Method | Result |
|---|---|
| `initialize` | `{protocolVersion: 1, agentCapabilities: {loadSession: true, promptCapabilities: {image: false, audio: false, embeddedContext: true}}, authMethods: [], agentInfo}` |
| `authenticate` | `{}` (HTTP auth already applies) |
| `session/new {cwd?, mcpServers?}` | `{sessionId, modes: {currentModeId, availableModes}}`; `mcpServers` are ignored and a differing `cwd` noted, both in `_meta.auoreo.warnings`; the handshake is audited as `acp.handshake` |
| `session/load {sessionId}` | `{}` if the session exists, else `-32602` |
| `session/prompt {sessionId, prompt}` | runs one non-interactive turn; `{stopReason}` when it ends (streams `session/update` first with `Accept: text/event-stream`) |
| `session/set_mode {sessionId, modeId}` | `{}`; `modeId` = `manual` \| `acceptEdits` \| `plan`, the session's permission mode for later turns (audited `acp.session.set_mode`) |
| `session/cancel {sessionId}` | notification; advisory and idempotent, never an error |
| `session/list {cursor?}` | `{sessions: [{sessionId, title, updatedAt}], nextCursor}` (advertised under `sessionCapabilities`) |
| `session/resume {sessionId}` | same as `session/load` (no history replay over `POST /acp`) |
| `session/close {sessionId}` | archives the session; unknown ids are an error (strict, unlike cancel) |

- `prompt` blocks: `text`, `resource` with `resource.text` (embedded context, wrapped in
  `<resource uri="…">`), `resource_link` (referenced by uri). Images/audio/blob resources are
  rejected with `-32602` (the capabilities say so).
- `stopReason`: `end_turn` (completed), `max_turn_requests` (step limit), `refusal`
  (blocked, e.g. by a hook or the budget), `cancelled` (`session/cancel`). A failed turn is
  a `-32603` error with the run id. The final text is in `_meta.auoreo.text`.
- **Streaming** (F3): send a single `session/prompt` request with
  `Accept: text/event-stream` and the response is SSE: `data:` lines carrying
  `session/update` notifications — `agent_message_chunk` (text deltas), `tool_call`
  (`status: pending`, `kind`, `rawInput`) then `tool_call_update` (`in_progress`, then
  `completed` or `failed` with the redacted result text), `plan` (from `update_plan` /
  `todo`) — and finally the JSON-RPC response for the request, after which the stream
  closes. A `: keepalive` comment is sent every 15 s of silence. There are no thinking events,
  so no `agent_thought_chunk`. Without the header the response is plain JSON as before; the
  same events are always on `GET /api/events?session_id=<sessionId>` (SSE) or `/ws`.
- **Handshake**: `initialize` stores the client's `protocolVersion`, `clientCapabilities`
  and `clientInfo`; `session/new` audits them with the agent version and an
  `ACP-Protocol-Version` header (if sent) as `acp.handshake`.
- **Extensions**: unknown `_`-prefixed methods are ignored as notifications and `-32601`
  as requests; both are counted (`routers.acp.stats()["ext_methods"]`) and audited once per
  name (`acp.ext.ignored`). `initialize` advertises `_meta.auoreo.extMethods: []`.
- Errors: invalid JSON `-32700`, malformed message/empty or oversized batch `-32600`,
  unknown method `-32601`, bad params `-32602`. Notifications get no response; a request
  made only of notifications answers `204`.

```bash
H=(-H "Authorization: Bearer $AUOREO_SERVER_PASSWORD" -H 'Content-Type: application/json')
curl -s localhost:4096/acp "${H[@]}" -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":1}}'
SID=$(curl -s localhost:4096/acp "${H[@]}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"'"$PWD"'","mcpServers":[]}}' | jq -r .result.sessionId)
curl -s localhost:4096/acp "${H[@]}" -d '{"jsonrpc":"2.0","id":3,"method":"session/prompt",
  "params":{"sessionId":"'"$SID"'","prompt":[{"type":"text","text":"hello"}]}}'
# {"jsonrpc":"2.0","id":3,"result":{"stopReason":"end_turn","_meta":{"auoreo":{…}}}}
curl -N localhost:4096/acp "${H[@]}" -H 'Accept: text/event-stream' -d '{"jsonrpc":"2.0","id":4,
  "method":"session/prompt","params":{"sessionId":"'"$SID"'","prompt":[{"type":"text","text":"hi"}]}}'
# data: {"jsonrpc":"2.0","method":"session/update","params":{"sessionId":"…","update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"echo: hi"}}}}
# data: {"jsonrpc":"2.0","id":4,"result":{"stopReason":"end_turn",…}}
```

## Not supported (yet)

- OpenAI: `/v1/completions`, `/v1/embeddings`, `/v1/responses`, `n > 1`, logprobs,
  `response_format` enforcement (passed nowhere), forced `tool_choice`.
- MCP server: writable resources, resources other than the four schemes above,
  `listChanged` on handshake-era sessions, sampling or elicitation towards the client; tools
  of agents other than `settings.agent`; deferred tools.
- ACP: `session/update` for requests sent without `Accept: text/event-stream` (use
  `/api/events`), streaming inside batches, `agent_thought_chunk`, permission requests to the
  client (`session/request_permission`), client-side file-system / terminal methods
  (`fs/*`, `terminal/*`), per-session MCP servers.

## Tracing and error reporting (F3)

- **Perfetto trace file**: `AUOREO_TRACE_FILE=/tmp/auoreo.trace.json` writes Chrome/Perfetto
  Trace-Event JSON (array form, one event per line, strict JSON once the process exits; a
  crashed file still opens in ui.perfetto.dev). Tracks `interaction` / `llm` / `tool`; `X`
  events for `agent.run` (model, agent, status), `llm.call` (model, purpose, token counts)
  and `tool.call` (tool, decision); instants for approvals and compactions. Never prompt
  text, tool arguments or results. Works with `AUOREO_OTEL_MODE=off`.
  `auoreo trace summarize <file> [--json]` prints count / total / p50 / p95 per span.
- **Sentry** (opt-in, `uv sync --extra sentry`): `AUOREO_SENTRY_DSN=<dsn>` and
  `sentry_share` = `errors_only` (default: exceptions only, no breadcrumbs, no traces) |
  `full` (also breadcrumbs without HTTP data, and performance traces only when
  `AUOREO_OTEL_MODE` is on) | `off` (nothing is sent). `send_default_pii=False`, no local
  variables, no request bodies/headers/cookies, no auto-enabled (AI/HTTP/DB) integrations;
  every event passes `core.redaction` in `before_send`. `sentry_sdk` is imported only by
  `core/observability.py`; `auoreo doctor`'s `otel` row reports the Sentry and trace-file
  state.
