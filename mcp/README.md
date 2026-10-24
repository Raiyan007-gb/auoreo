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
```
