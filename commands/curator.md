---
name: curator
description: Review what long-term memory holds (curation itself is deterministic — `auoreo curator run`).
handler: prompt
args_hint: "[topic]"
---
Use `knowledge_search` to review what long-term memory holds about: {{args}}
(if no topic was given, search for the user's preferences, the project and recent decisions).
Summarize the facts you find in a short list with their `fact:<id>` citations, and point out
any that look outdated, duplicated or contradictory. Do not change memory unless I ask; the
curator pass that archives and merges facts runs on its own (`auoreo curator run`).
