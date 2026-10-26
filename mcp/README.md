# mcp/

`mcp/<name>.yaml` declares an MCP server. Its tools are registered as
`mcp__<server>__<tool>` and go through the same permission engine as built-in tools.

```yaml
name: fetch
transport: stdio               # stdio | streamable-http | sse (legacy HTTP+SSE)
command: uvx
args: [mcp-server-fetch]
env: {API_KEY: "secret:fetch/api_key"}   # secret:<name> resolves via the secret store
timeout_ms: 30000
enabled: false
exposure: deferred             # deferred tools are found through tool_search
tools:                         # per remote tool overrides (server tool name)
  slow_search: {timeoutMs: 120000}
sampling:                      # server→client sampling/createMessage (default off)
  enabled: false
  max_tokens: 1024             # caps the server's own maxTokens
  model: null                  # unset → modelPreferences / aux routing (`auxiliary.tasks.sampling`)
roots:                         # client roots (deprecated by MCP 2026-07-28; compat shim, default off)
  enabled: false               # true → [session cwd, *paths] as file:// roots
  paths: []                    # extra allow-listed directories (realpath'd, must exist)
```

Sampling requests run as one aux gateway call (`tool_choice: none`), rate-limited by
`tool_engine.sampling_rpm`, charged to the usage ledger and audited `mcp.sampling`. They need
permission `mcp_sampling` (pattern = server name): an `allow` rule skips the prompt, otherwise
the first request per session asks the user; non-interactive runs refuse.

With `tool_engine.error_on_collision: true` an MCP tool whose name clashes with another
definition is skipped and listed in the harness errors (default: builtins win, warning).

## Bundles (spec §25.15 L1072)

`filesystem` (npx `@modelcontextprotocol/server-filesystem`, roots on), `fetch` (uvx
`mcp-server-fetch`), `time` (uvx `mcp-server-time`) and `memory` (npx
`@modelcontextprotocol/server-memory`) ship here, all `enabled: false`. Turn one on with
`auoreo mcp add --bundle <name>` (copies it into `~/.auoreo/mcp/` with `enabled: true`; add
`--no-check` to skip the live connection test). No bundle carries credentials.

## Client behaviour (I6)

- **`tools/list_changed`**: pushed on legacy connections, or received on a
  `subscriptions/listen` stream on 2026-07-28 connections (`i6.mcp.listen_tools_changed`) →
  the server's tools are re-listed and re-registered (harness generation bump) and
  `mcp.tools_changed {server, tools, generation}` is emitted. Bursts are debounced
  (`i6.mcp.refresh_debounce_s`).
- **Roots**: with `roots.enabled`, the calling session's cwd plus `roots.paths` are offered as
  `file://` roots; a tool call from another cwd sends `notifications/roots/list_changed`.
- **Sampling with tools**: the server's `tools` / `toolChoice` are passed to the aux call and
  tool calls come back as `tool_use` content (`stopReason: toolUse`). Each MCP tool call may
  sample at most `i6.mcp.sampling_iterations` (3) times; the last pass runs with
  `toolChoice: none`, further requests are refused. `modelPreferences` pick the aux model:
  hints (substring of a configured routing group/deployment, never the main model) first,
  then the dominant priority (`intelligence` → `smart|large|reasoning`, `cost` →
  `cheap|small`, `speed` → `fast|small|cheap` when configured), else aux routing.
