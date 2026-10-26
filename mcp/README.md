# mcp/

`mcp/<name>.yaml` declares an MCP server. Its tools are registered as
`mcp__<server>__<tool>` and go through the same permission engine as built-in tools.

```yaml
name: fetch
transport: stdio               # stdio | streamable-http
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
  model: null                  # unset → aux routing (`auxiliary.tasks.sampling`)
```

Sampling requests run as one aux gateway call (`tool_choice: none`), rate-limited by
`tool_engine.sampling_rpm`, charged to the usage ledger and audited `mcp.sampling`. They need
permission `mcp_sampling` (pattern = server name): an `allow` rule skips the prompt, otherwise
the first request per session asks the user; non-interactive runs refuse.

With `tool_engine.error_on_collision: true` an MCP tool whose name clashes with another
definition is skipped and listed in the harness errors (default: builtins win, warning).
