---
name: mcp
description: Wire Model Context Protocol (MCP) servers and clients into the platform with the official Python SDK — FastMCP tools over stdio/SSE/streamable-HTTP, typed tool declarations, and client connections. Use when building agent_mcp and agent_tools in this repo.
---

# Model Context Protocol (MCP) — Python SDK

Spec: https://modelcontextprotocol.io · SDK repo: https://github.com/modelcontextprotocol/python-sdk (package `mcp`) · Context7: `/modelcontextprotocol/python-sdk` (v1.x, benchmark 89).

## Two roles in this repo

- **`agent_tools`** — declare the platform's own typed tools (and run external MCP tools via a client).
- **`agent_mcp`** — (a) client: connect to external MCP servers (stdio/SSE/HTTP) and expose their tools to the agent; (b) server: expose the whole agent as an MCP server so other agents/apps can drive it.

## Declaring a tool (server side)

The Python SDK's historical `FastMCP` is now the **`MCPServer`** class (`FastMCP` is aliased to it —
both import paths work, but current docs favor `MCPServer`). The `@tool()` decorator must be
**called** (`@mcp.tool()`) and accepts keyword args (`name`, `title`, `description`, `annotations`,
`icons`, `meta`, `structured_output`):

```python
from mcp.server import MCPServer      # MCPServer (FastMCP is the legacy alias)
from mcp.server.fastmcp import FastMCP  # still valid — same class

mcp = MCPServer("agent-platform")

@mcp.tool(
    name="web_search",
    description="Search the web. Returns ranked, summarized results.",
)
def web_search(query: str, num_results: int = 8) -> str:
    return run_search(query, num_results)

mcp.run()  # default transport: stdio
```

- Typed parameters (int, str, custom Pydantic) → tool schema is derived automatically.
- Tools can request a `Context` object by adding it as a parameter (for logging/request context).
- Runs: `mcp.run()` (stdio), `mcp.run(transport="sse", host=..., port=...)`, or `mcp.run(transport="streamable-http", ...)`. Note: `run()` is synchronous.
- This is exactly how opencode's `web_search` works — it calls **Exa/Parallel MCP endpoints** (`https://mcp.exa.ai/mcp`, `https://search.parallel.ai/mcp`). This repo's `agent_tools/web_search.py` should do the same: prefer an MCP-backed search provider behind a `SearchProvider` interface.

## Client side (call tools from the agent)

```python
from mcp.client.stdio import stdio_client, StdioServerParameters

server_params = StdioServerParameters(
    command="python", args=["-m", "path.to.search_server"], env=None
)

async with stdio_client(server_params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools_result = await session.list_tools()   # returns ListToolsResult
        for tool in tools_result.tools:             # iterate .tools (not the bare result)
            print(tool.name, tool.description, tool.inputSchema)
        result = await session.call_tool("web_search", {"query": "postgres pgvector", "num_results": 5})
        # result.content / result.isError
```

- For in-process tests, connect the SDK's **in-memory `Client`** directly to the server object (no subprocess):

```python
import pytest
from mcp import Client
from server import mcp            # your MCPServer/FastMCP instance

@pytest.mark.anyio
async def test_call_tool(client: Client):
    result = await client.call_tool("add", {"a": 1, "b": 2})
    # A plain-str return is wrapped as TextContent(type="text", text="3"); on v1.12+,
    # result.meta carries a server identity stamp — clear it before full-result asserts.
    result.meta = None
    assert result.content[0].text == "3"

@pytest.fixture
async def client():
    async with Client(mcp, raise_exceptions=True) as c:   # raise_exceptions surfaces tool errors
        yield c
```

## Patterns for this repo

1. **Registry, not ad-hoc wiring:** keep an MCP server registry (`agent_mcp/registry.py`) mapping server name → transport config → configured tools, mirroring opencode's `mcp/*` package + `.opencode` config `mcp` map.
2. **Single tool abstraction to the agent:** whatever MCP tool arrives, wrap it into the repo's `AnyTool` shape (`agent_tools/tool.py`) so the LangGraph runtime and permission system see one interface. Don't pass raw MCP tool objects through `agent_runtime`.
3. **Permissions first:** MCP-discovered tools must pass the same `allow/deny` ruleset as built-ins (plan.md Part H #10). Never grant tool access implicitly.
4. **Auth:** some remote MCP servers need OAuth (`agent_mcp/oauth.py`, opencode has this too). Support env-provided keys and an OAuth flow; never hardcode creds.
5. **Server mode (Phase 3):** expose `GET /.well-known/agent.json`-style discovery and an MCP endpoint (SEE/streamable-HTTP) so external orchestrators can drive the agent. Reuse the same `session` + `input` admission path as the HTTP API — MCP tools are a *transport* over the same core, not a separate code path.

## Transports (choose by consumer)

| Transport | Use when |
|---|---|
| `stdio` | Local servers (agent spawns subprocess); default, simplest |
| `sse` | Remote servers with server-sent upgrade events |
| `streamable-http` | Modern HTTP (current recommended for remote); supersedes SSE streaming transport |