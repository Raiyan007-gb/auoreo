# commands/

Slash commands: one `commands/<name>.md` file per command. Typing `/name rest of line`
as a chat message triggers it; an unknown `/word` is sent unchanged as a normal message.

```markdown
---
name: review              # must match the file name
description: Review code with the code-review skill.
handler: prompt           # prompt | compact | model | plan
args_hint: <path>         # shown by UIs and `GET /api/commands`
---
Load the `code-review` skill and review: {{args}}
```

| handler | behaviour |
|---|---|
| `prompt` | the body is rendered (`{{args}}` = rest of the line, `{{input}}` = the whole message) and sent as the chat message |
| `model` | sets the session's model / routing group to the args (`session.model_changed`); no turn runs |
| `plan` | runs one turn with the `planner` agent; the rendered body (default `{{args}}`) is the message |
| `compact` | asks the compaction service to summarize older turns now (`run.failed{UNSUPPORTED}` when unavailable) |

`subtask: true` (G6) runs the rendered body as a **background child session** with `agent:`
(the parent's context stays clean; the result returns as a task-notification). With
`worktree: true` the child gets an isolated git worktree under `<cwd>/.auoreo/worktrees/`
(git-ignored) holding HEAD plus the tracked working changes; `{{worktree}}` in the body is
its path (the cwd otherwise). Without the teams extension the body runs inline. `/review`
is such a command (agent `reviewer`); `/babysit_pr <n>` loads the `babysit-pr` skill.

Like every other folder kind, `~/.auoreo/commands/` and `<cwd>/.auoreo/commands/` override
bundled commands of the same name.
