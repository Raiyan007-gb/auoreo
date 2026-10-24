# tools/

One folder per tool: `tools/<name>/tool.yaml` + a handler file.

```yaml
name: hello                    # must match the folder name
description: Say hello.
parameters:                    # JSON Schema (type: object); validated before the handler runs
  type: object
  properties: {who: {type: string}}
  required: [who]
  additionalProperties: false
handler: handler.py:run        # async def run(args: dict, ctx: ToolContext) -> str | ToolResult
permission: hello              # permission name for rules (default: tool name)
pattern_arg: who               # argument used as the rule pattern
default_action: allow          # when no rule matches: allow | ask | deny
is_read_only: true
is_concurrency_safe: true      # may run in parallel with other safe calls
exposure: direct               # direct | deferred (found via tool_search) | hidden
timeout_ms: 30000
```

Handlers import types from `auoreo.tools.context` (`ToolContext`, `ToolResult`,
`ToolError`). Raise `ToolError("message")` for a model-visible error. A tool in an
overlay root that shadows a bundled tool must set `override: true`.
