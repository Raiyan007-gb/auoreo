---
name: code-review
description: Review a code change for correctness bugs, security issues and missing tests. Use when asked to review a diff, a file, or "check this before I merge".
---
# Code review

1. Get the change: `git diff` (or the files named by the user). Read every changed file in full, not just the hunks.
2. For each change, trace a realistic input through it. Look for: wrong conditions, off-by-one, unhandled errors, resource leaks, injection (shell/SQL/path), secrets in code, race conditions.
3. Check tests: is the new behaviour covered? Run the project's test command.
4. Report findings ranked by severity as `path:line — problem — concrete failure scenario — suggested fix`. Say plainly when you found nothing.
