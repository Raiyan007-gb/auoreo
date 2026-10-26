---
name: babysit_pr
description: Watch a pull request through review — fix actionable threads, push, reply, resolve — with the babysit-pr policy.
handler: prompt
args_hint: <PR number or URL>
---
Load the `babysit-pr` skill and babysit pull request {{args}} following its review-thread
policy exactly. Stop and ask me before anything the policy marks as needing a human.
