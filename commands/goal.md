---
name: goal
description: Create or update a long-running goal from this message.
handler: prompt
args_hint: <goal, or progress on an existing goal>
---
Manage my long-running goals. Use `tool_search` with "goal" if `get_goal`, `create_goal`
and `update_goal` are not loaded yet.

1. Call `get_goal` to list the active goals.
2. If the text below is about one of them, call `update_goal` on it (status, progress 0..1,
   or `absorb_into` when two goals are the same). Otherwise call `create_goal` with a short
   title and put the details in the description.
3. Reply with one line: the goal id, title, status and progress.

{{args}}
