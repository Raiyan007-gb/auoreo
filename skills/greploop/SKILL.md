---
name: greploop
description: Grep-then-fix loop for a bug pattern that may repeat across the codebase. Use when a fix or review comment says "same problem elsewhere" or you must remove every instance of a pattern; bounded to 5 iterations and 5 files.
---
# greploop — grep → fix → re-grep, bounded

Use it to eliminate every occurrence of one concrete pattern (a wrong call, an unsafe idiom,
a renamed API) without wandering off.

## Budget (hard limits)

- **At most 5 iterations** of the loop below.
- **At most 5 files changed** in total. If the grep finds more, fix the 5 most important,
  then stop and report the rest as a list (`path:line`) for the user to decide.

## Loop

1. **Define the pattern** precisely as a regex and write down what a correct occurrence looks
   like (so true negatives are not "fixed"). Example: `subprocess\.run\([^)]*shell=True`.
2. **Grep** with `grep` (output mode `content`, include line numbers); exclude vendored,
   generated and test-fixture folders unless they are in scope.
3. **Classify** each hit: *bug* (fix), *intended* (leave, note why), *unsure* (ask).
4. **Fix** the bug hits with minimal edits (`edit` / `apply_patch`), one file at a time.
5. **Verify**: run the tests/linters that cover the touched files.
6. **Re-grep** the same pattern. Done when it returns only *intended* hits ("5/5": the
   last pass found nothing new to fix). Otherwise continue with the next iteration.

## Report

Iterations used (n/5), files changed (n/5), every remaining hit with its classification,
and the verification commands with their results. Never claim "all fixed" while the last
grep still shows a *bug* hit.
