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

## Toolsets

`toolset:` groups tools (`files`, `shell`, `planning`, `github`, `coding`, …) for listing and
for agents that enable whole groups. New tools should be `exposure: deferred` unless they are
core: deferred tools stay out of the system prompt until `tool_search` loads them. The
`github` toolset (`gh_*`), `pwsh`, `run_tests`, `run_lint` and `verify_plan` are described in
[`docs/coding-tools.md`](../docs/coding-tools.md).

## Terminals, jobs, monitor, ops probes, caps (G4b)

- `terminal_pty` opens a persistent local PTY (`bash` permission on `cmd`, refused under an
  enforcing sandbox like `exec_command`); `terminal_pty_send/read/signal/close/list` drive it.
  `send` rides on the opening grant but `bash stdin:<id>` denies and the bash floors apply to
  every typed line. One lock per PTY; PTYs die with their session (archive/delete/idle).
- Background jobs are a session's still-running `exec_command` processes (start one with
  `yield_time_ms: 0`) plus its PTYs: `jobs_list`, `jobs_output` (poll), `jobs_kill`.
- `monitor` streams a command, polls one every `every_s` (through the sandbox), or follows a
  file until an `until` regex matches or `timeout_s` passes, emitting `tool.progress`.
- `ops_gpu` (NVML via `nvidia-ml-py`) and `ops_ping` (ICMP via `ping3`, TCP-connect fallback)
  import their libraries lazily (extra `ops`) and report "unavailable" honestly instead of
  failing. The manifest has no `check_fn` availability gate yet, so they stay listed.
- Caps (`settings.tools`): `web_fetch` 5 MiB body (DNS resolved once, validated, pinned —
  see `auoreo.tools.net`); `doc_read`/`sheet_read` 20 MiB files; `sheet_read engine: pandas`
  (or `tabular_pandas: true`, extra `tabular`) parses with pandas; `web_search timeout_s`
  per backend up to 60 s; `delegate_task tasks[]` runs 3 children at a time.
