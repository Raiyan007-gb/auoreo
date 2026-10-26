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
net: none                      # none | proxy | direct — egress class, recorded in the audit row
sandbox_mode: workspace-write  # read-only | workspace-write | danger-full-access (bash runs
                               # through the selected `sandbox` backend with this mode)
```

Handlers import types from `auoreo.tools.context` (`ToolContext`, `ToolResult`,
`ToolError`). Raise `ToolError("message")` for a model-visible error. A tool in an
overlay root that shadows a bundled tool must set `override: true`.

**Floors before the prompt (`pre_check`).** A handler module may also define a sync
`def pre_check(args: dict) -> str | None`. The executor calls it after schema validation and
`PreToolUse` hooks but *before* permission evaluation; a non-`None` string refuses the call
(audited `tool.denied`, reason `pre_check`) without any rule, mode or approval prompt being
consulted. Use it for argument-level floors a permission pattern cannot express (e.g.
`computer` key combos); keep the same check in the handler as defence in depth.

## Availability, results and progress (G4a)

```yaml
is_enabled: true               # false hides the tool (listed in /api/harness with the reason)
check_fn: handler.py:available # sync probe: True/None = available, False/"reason" = hidden;
                               # `pkg.module:fn` also works; cached tool_engine.check_fn_ttl_s (30 s)
requires_env: [EXA_API_KEY]
env_gate: all                  # none (default: pass-through allowlist only) | all | any —
                               # hide the tool when env/secret store lacks the names
emoji: "🔎"
output_schema: {type: object}  # validates ToolResult.meta["structured"] (else JSON content)
```

- Arguments are parsed losslessly: duplicate keys, `NaN`/`Infinity`, integers beyond 2^53−1
  and non-object payloads are refused with `InvalidArgumentsError: <tool>: …` (never
  coerced). Handlers get their own copy; hooks and audit see a detached snapshot.
- `await ctx.progress("message", percent=40)` (alias `ctx.on_progress`) emits a
  `tool.progress` event, at most one per `tool_engine.progress_interval_ms` (a final
  `percent=100` always goes out).
- Writers lock every path they touch (sorted, `Semaphore(1)` each): a `path` pattern
  argument, the file headers of a `patch` argument, or the handler module's optional
  `touched_paths(args) -> list[str]`.
- Results over the inline cap spill to `sessions/<id>/tool-results/<call>.txt`, at most
  `tool_engine.spill_max_bytes` (64 MB); a cut spill is noted in the result.
- `tool_describe` / `tool_call` reach deferred tools without loading them; `tool_call` runs
  the target through the same validation, hooks, permission and approval path.

## Toolsets

`toolset:` groups tools (`files`, `shell`, `planning`, `github`, `coding`, …) for listing and
for agents that enable whole groups. New tools should be `exposure: deferred` unless they are
core: deferred tools stay out of the system prompt until `tool_search` loads them. The
`github` toolset (`gh_*`), `pwsh`, `run_tests`, `run_lint` and `verify_plan` are described in
[`docs/coding-tools.md`](../docs/coding-tools.md).
