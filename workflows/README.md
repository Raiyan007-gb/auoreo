# workflows/

`workflows/<name>.yaml` chains agents. Each step runs a fresh agent turn; its final answer
is available to later steps as `{{steps.<id>.output}}`. `{{input}}` is the user message.

```yaml
name: plan-then-build
description: Plan with the planner agent, then implement with the default agent.
steps:
  - {id: plan, agent: planner, input: "{{input}}"}
  - {id: build, agent: default, input: "Implement this plan:\n{{steps.plan.output}}"}
```
