---
name: mcp
description: Wire Model Context Protocol (MCP) servers and clients into the platform with the official Python SDK v2 — MCPServer (formerly FastMCP) tools over stdio/streamable-HTTP, typed tool declarations, and Client connections. Use when building agent_mcp and agent_tools in this repo.
---

# Model Context Protocol (MCP) — Python SDK

Spec: https://modelcontextprotocol.io · SDK repo: https://github.com/modelcontextprotocol/python-sdk (package `mcp`) · Context7: `/modelcontextprotocol/python-sdk` (v2.x — docs checked at v2.3.0; v2 docs site: https://py.sdk.modelcontextprotocol.io/v2).

## Two roles in this repo

- **`agent_tools`** — declare the platform's own typed tools (and run external MCP tools via a client).
- **`agent_mcp`** — (a) client: connect to external MCP servers (stdio/streamable-HTTP, legacy SSE) and expose their tools to the agent; (b) server: expose the whole agent as an MCP server so other agents/apps can drive it.

## Declaring a tool (server side)

In SDK v2 the v1 `FastMCP` class is renamed **`MCPServer`**, and the old path is **gone, not
aliased**: importing `mcp.server.fastmcp` raises `ModuleNotFoundError` (submodules moved to
`mcp.server.mcpserver.*`; `FastMCPError` → `MCPServerError`; `ctx.fastmcp` → `ctx.mcp_server`;
`get_context()` removed — declare a `ctx: Context` param). The `@tool()` decorator must be
**called** (`@mcp.tool()`) and accepts the same keyword args as v1 (`name`, `title`, `description`,
`annotations`, `icons`, `meta`, `structured_output`):

```python
from mcp.server import MCPServer      # v1 was: from mcp.server.fastmcp import FastMCP
# (also: from mcp.server.mcpserver import MCPServer, Context)

mcp = MCPServer("agent-platform")     # name/instructions/lifespan/auth only — no host/port here

@mcp.tool(
    name="web_search",
    description="Search the web. Returns ranked, summarized results.",
)
def web_search(query: str, num_results: int = 8) -> str:
    return run_search(query, num_results)

if __name__ == "__main__":
    mcp.run()  # default transport: stdio (blocks)
```

- Typed parameters (int, str, custom Pydantic) → tool schema is derived automatically.
- Tools can request a `Context` object by adding a `ctx: Context` parameter (for logging/request context).
- Runs: `mcp.run()` (stdio) or `mcp.run(transport="streamable-http", host="127.0.0.1", port=8000, streamable_http_path="/mcp", stateless_http=..., json_response=...)`. **All transport options go to `run()`** — `MCPServer("x", port=9000)` is a `TypeError` in v2. `run()` is synchronous; keep it under `if __name__ == "__main__":`. To serve inside an existing ASGI app (e.g. `agent_api`), mount the server's ASGI app instead (`mount_path` was removed).
- Tool results: `content` (for the model), `structured_content` (typed JSON for code, from the return type), `is_error`. A raising tool comes back as `is_error=True`, not an exception.
- This is exactly how opencode's `web_search` works — it calls **Exa/Parallel MCP endpoints** (`https://mcp.exa.ai/mcp`, `https://search.parallel.ai/mcp`). This repo's `agent_tools/web_search.py` should do the same: prefer an MCP-backed search provider behind a `SearchProvider` interface.

## Client side (call tools from the agent)

```python
from mcp import Client, StdioServerParameters

server_params = StdioServerParameters(
    command="python", args=["-m", "path.to.search_server"]  # env= ADDS to an allow-listed env
)

# Client(...) picks the transport: StdioServerParameters → subprocess,
# "https://host/mcp" (str) → streamable HTTP, server object → in-memory.
async with Client(server_params) as client:                 # handshake done on enter
    tools_result = await client.list_tools()                # ListToolsResult; paginate with cursor=/next_cursor
    for tool in tools_result.tools:                         # iterate .tools (not the bare result)
        print(tool.name, tool.description, tool.input_schema)   # v2 fields are snake_case
    result = await client.call_tool("web_search", {"query": "postgres pgvector", "num_results": 5})
    # result.content / result.structured_content / result.is_error
```

- Remote servers: `Client("https://mcp.exa.ai/mcp")`. Headers/auth/timeouts go on an `httpx2.AsyncClient` passed to `streamable_http_client(url, http_client=...)` (there is no `headers=` kwarg); hand that transport to `Client(...)`.
- `stdio_client(params, errlog=...)` (from `mcp`) is only needed to redirect the child's stderr: `Client(stdio_client(params, errlog=f))`.
- Many servers at once: `ClientSessionGroup` (`connect_to_server(StdioServerParameters | StreamableHttpParameters)`, merged `group.tools`, `group.call_tool(name, args)`); tool names must be unique across the group (collisions raise `MCPError`).

- For in-process tests, connect the SDK's **in-memory `Client`** directly to the server object (no subprocess; replaces v1's removed `create_connected_server_and_client_session`):

```python
import pytest
from mcp import Client
from server import mcp            # your MCPServer/FastMCP instance

@pytest.mark.anyio
async def test_call_tool(client: Client):
    result = await client.call_tool("add", {"a": 1, "b": 2})
    assert result.structured_content == {"result": 3}   # typed return → structured_content
    assert result.content[0].text == "3"                # model-facing TextContent

@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:   # raise_exceptions surfaces server errors (in-process only)
        yield c
```

- `Client(server)` negotiates protocol **2026-07-28** by default, which has no server-initiated requests: tools using `ctx.elicit()` / sampling / roots fail with `NoBackChannelError`. For those (e.g. HITL elicitation), pass `mode="legacy"` plus the callbacks, or redesign the flow.

## Patterns for this repo

1. **Registry, not ad-hoc wiring:** keep an MCP server registry (`agent_mcp/registry.py`) mapping server name → transport config → configured tools, mirroring opencode's `mcp/*` package + `.opencode` config `mcp` map.
2. **Single tool abstraction to the agent:** whatever MCP tool arrives, wrap it into the repo's `AnyTool` shape (`agent_tools/tool.py`) so the LangGraph runtime and permission system see one interface. Don't pass raw MCP tool objects through `agent_runtime`.
3. **Permissions first:** MCP-discovered tools must pass the same `allow/deny` ruleset as built-ins (plan.md Part H #10). Never grant tool access implicitly.
4. **Auth:** some remote MCP servers need OAuth (`agent_mcp/oauth.py`, opencode has this too). Support env-provided keys and an OAuth flow; never hardcode creds.
5. **Server mode (Phase 3):** expose `GET /.well-known/agent.json`-style discovery and an MCP endpoint (streamable-HTTP) so external orchestrators can drive the agent. Reuse the same `session` + `input` admission path as the HTTP API — MCP tools are a *transport* over the same core, not a separate code path.

## Transports (choose by consumer)

| Transport | Use when |
|---|---|
| `stdio` | Local servers (agent spawns subprocess); default, simplest |
| `streamable-http` | Anything remote/deployed (recommended); default endpoint `/mcp` on port 8000 |
| `sse` | Legacy only — superseded by streamable HTTP in protocol rev 2025-03-26; still runs (`transport="sse"`) for old clients, don't build new on it |