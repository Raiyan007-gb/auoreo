# hooks/

`hooks/<name>.yaml` runs a command at a lifecycle event. The event is passed as JSON on
stdin; the command answers on stdout.

```yaml
name: lint-after-edit          # must match the file name
description: Run ruff on edited Python files.
event: PostToolUse             # PreToolUse | PostToolUse | PermissionRequest | UserPromptSubmit
                               # | SessionStart | SessionEnd | PreCompact | PostCompact | Stop
matcher: "edit|write"          # fnmatch over the tool name (or agent name for session events)
command: ["./scripts/lint-hook.sh"]   # argv, no shell; cwd = session cwd; scrubbed env
timeout_ms: 5000
on_error: warn                 # warn | block (a crashing/timeout hook blocks the action)
enabled: true
```

Replies: tool events `{"decision": "allow"|"deny"|"modify", "message"?: str, "input"?: {...}}`
(`modify` replaces the tool arguments before execution); `Stop` events
`{"block": true, "reason": "..."}` append the reason and let the agent continue. Hooks never
run on API-error messages.

Details: `matcher` accepts `a|b` alternatives; a `command` argument starting with `./` or
`../` resolves against the root the hook file lives in (bundled, project or runtime) when
that file exists there, else against the session cwd. The subprocess gets
`AUOREO_SESSION_ID` and `AUOREO_HOOK_EVENT` in its (scrubbed) environment. Several hooks on
one event run in name order; `modify` results chain and the first `deny`/`block` wins.
`UserPromptSubmit` may answer `{"decision": "modify", "text": "..."}` to rewrite the prompt
or `deny` to block the turn (`run.blocked`, code `HOOK_DENIED`).
