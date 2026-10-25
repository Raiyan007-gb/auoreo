# Spike: ACP changelog (spec L1039, 2 h)

**Decision:** stay on ACP **protocol version 1**. Implement the three small, safe v1 session
methods (`session/list`, `session/resume`, `session/close`) and advertise them under
`agentCapabilities.sessionCapabilities`. Document the remaining deltas. Most of them need a
bidirectional channel (agent → client requests), which `POST /acp` does not have.

## Source

Agent Client Protocol repository via Context7 (`/agentclientprotocol/agent-client-protocol`):
`schema/v1/meta.json` (the stable v1 method list), the `agent-client-protocol-schema` v1 Rust
types, `schema/v2/schema.json`, and the RFDs `session-list`, `session-close`,
`session-delete`, `session-config-options`, `mcp-over-acp` and `additional-directories`.
These were compared with `src/auoreo/api/routers/acp.py` as of `ca08324`.

## Deltas

| Area | Spec v1 (current) | AUOREO before | Now |
|---|---|---|---|
| `session/list` (agent) | stable; `sessionCapabilities.list`; `{cwd?, cursor?}` → `{sessions: [{sessionId, cwd, title?, updatedAt?}], nextCursor?}` | missing (-32601) | ✅ implemented: newest first, 50 per page, opaque offset cursor; a `cwd` other than the server's matches nothing (all sessions share it) |
| `session/resume` (agent) | stable; `sessionCapabilities.resume`; resume without history replay | missing | ✅ same as `session/load` here (neither replays history); unknown session → -32602 |
| `session/close` (agent) | stable; `sessionCapabilities.close`; cancel work, free resources; may error for unknown sessions | missing | ✅ cancels any running turn; unknown session → -32602 (unlike the advisory, idempotent `session/cancel`) |
| `agentCapabilities.sessionCapabilities` | object of optional per-feature objects | absent | ✅ `{list: {}, resume: {}, close: {}}` |
| `session/load` history replay | the agent replays the conversation as `session/update` notifications before answering | answers `{}` with no replay | documented: it would need the SSE response mode on `session/load` too, mapping stored messages to `user_message_chunk`/`agent_message_chunk`. Follow-up |
| `session/set_config_option` (+ `configOptions` in `session/new`) | stable; generic per-session options (mode, model, …) | only `session/set_mode` | documented: AUOREO's natural options are `mode` (exists) and `model` (`/model`); a small follow-up once a client uses it |
| `session/delete` | stable; `sessionCapabilities.delete` | missing | not done: destructive. AUOREO archives rather than deletes from the UI; mapping delete → archive would be surprising. Decide with product |
| `logout` | stable | missing | not done: no `authMethods` are offered (the HTTP Bearer token authenticates), so there is nothing to log out of |
| `$/cancel_request` (protocol) | stable notification for cancelling an in-flight request | ignored (unknown notifications are dropped) | unchanged: a `session/prompt` is cancelled through `session/cancel`/`close` |
| `session/request_permission`, `fs/*`, `terminal/*`, `elicitation/*` (client methods) | agent → client requests | not used: tools needing approval are denied in ACP turns; files and shell run server side | architectural: needs a bidirectional transport (stdio or WebSocket). Over `POST /acp` + SSE there is no way to receive the client's answer. The bridge to build is ACP over `/ws` or an `auoreo acp` stdio command |
| `mcpCapabilities` (`http`, `sse`, `acp`) | optional; omitted = false | omitted; `mcpServers` ignored with a warning | unchanged (correct by omission) |
| `promptCapabilities.image` etc. | v1 booleans (v2 makes them objects) | booleans | unchanged; v2 is a draft and AUOREO stays on v1 |
| `additionalDirectories` on `session/new`/`load` (RFD) | not in stable v1 | ignored | unchanged |

## Measurements / tests (this container)

- `tests/integration/test_acp_sessions.py` (3 tests): list pagination with `nextCursor`, the
  `cwd` filter, a bad cursor → -32602, and resume/close known vs unknown.
- `tests/integration/test_acp.py::test_initialize` now expects `sessionCapabilities`.
- ACP suites (`test_acp.py`, `test_acp_sessions.py`, `test_acp_stream.py`): 17 passed.
- `acp.py` stays under the 500-line limit (494 lines).

## Follow-ups

1. ACP over a bidirectional transport (`auoreo acp` stdio and/or `/ws` subprotocol). That
   unlocks `session/request_permission` (interactive approvals in the editor), `fs/*` (the
   editor's unsaved buffers) and `terminal/*`.
2. `session/load` history replay in SSE mode.
3. `session/set_config_option` with `mode` and `model` options, plus `configOptions` in
   `session/new`.
4. `docs/compat.md` § ACP should list the three new methods (not edited here: outside this
   workstream's files).
