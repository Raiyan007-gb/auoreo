---
name: babysit-pr
description: Shepherd an open pull request through review and CI with a strict review-thread policy. Use when asked to babysit, watch, or drive a PR to green and zero unresolved comments.
---
# Babysit a pull request

Goal: CI green and **zero unresolved review threads**, without ever overriding a human.

## Loop (one pass per call; repeat until done or blocked)

1. **State** — `gh_pr_view` (title, head branch, mergeable, review decision), `gh_pr_checks`
   (failing jobs), and the review threads via `gh_api`
   (`repos/{owner}/{repo}/pulls/{n}/comments` and the GraphQL `reviewThreads` with `isResolved`).
2. **Triage every unresolved thread** with the policy below. Ignore resolved threads and
   outdated ones whose code no longer exists.
3. **Fix** the actionable ones in the PR's branch (work in an isolated worktree when the
   checkout has other changes: `/review` style, `git worktree add`). One logical fix per thread;
   run the tests that cover it (`greploop` skill for "same bug elsewhere" comments).
4. **Push**, then reply on each thread you fixed: `[oreo] Fixed in <short-sha>: <one line>`.
   Resolve the thread **only after the push** that contains the fix (resolve-after-push).
5. **CI** — for each failing check read the log (`gh_run_view`), fix the cause (never skip,
   retry-loop or delete a test to go green), push, re-check.
6. Report: threads fixed / answered / waiting on a human, checks status, next step.

## Review-thread policy

| Thread | Action |
|---|---|
| Concrete bug, failing test, typo, missing check | Fix, push, reply `[oreo] Fixed in <sha>`, then resolve |
| Style nit that matches the repo's conventions | Fix (cheap) and resolve after push |
| Question about intent | Reply with the answer and a code pointer; leave it for the reviewer to resolve |
| Design disagreement, scope change, "should we…" | Do **not** change code; summarise both options and ask the user |
| Comment by a bot (linters, coverage) | Fix if real; otherwise reply `[oreo] Not applicable: <reason>` |
| Already resolved, or by the PR author to themselves | Ignore |

Never resolve a thread opened by a human reviewer without a pushed fix; never dismiss reviews,
force-push over others' commits, merge, or change branch protection — those need the user.
Every comment you post starts with `[oreo]` so humans can tell it apart.

## Done when

All checks pass, every thread is resolved or explicitly waiting on a human, and you have told
the user which threads need them.
