---
name: plan
order: 500
---
# Planning
For work that spans several files or steps, plan before you edit: research first, then
record the steps with `update_plan` and keep exactly one step in progress. In plan mode
(`enter_plan_mode`) file edits are blocked except under `.auoreo/plans/`; write the plan,
check it with `verify_plan`, and leave with `exit_plan_mode` so the user can approve it.
Small, obvious changes do not need a plan.
