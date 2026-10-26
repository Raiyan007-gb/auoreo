---
name: review
description: Review the current changes in an isolated git worktree (background subtask with the reviewer agent).
handler: prompt
args_hint: <path, diff or focus>
subtask: true
agent: reviewer
worktree: true
---
Load the `code-review` skill and review: {{args}}

You are in an isolated git worktree at `{{worktree}}` that holds the current HEAD plus the
uncommitted changes. Work only there: read files by their absolute path under `{{worktree}}`
and run every command as `cd {{worktree}} && …`. Never touch the original checkout.

1. Research: `cd {{worktree}} && git status --short && git diff HEAD` (and `git log -5 --oneline`)
   to learn what changed and why; read the surrounding code.
2. Verify: run the relevant tests and linters in the worktree and report the exact commands
   and their results.
3. Verdict first (`approve` or `changes needed`), then each finding as
   `path:line — problem — suggested fix`, most important first.
