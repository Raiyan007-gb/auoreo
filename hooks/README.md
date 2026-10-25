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
