# agents/

One folder per agent: `agents/<name>/AGENT.md`. YAML frontmatter configures the agent,
the markdown body is its instructions (rendered into the `agent` prompt section).

```yaml
---
name: reviewer                 # must match the folder name
description: Reviews diffs.
model: small                   # optional: alias or routing group
tools: {allow: ["read", "grep", "glob", "bash"], deny: []}   # fnmatch globs
skills: ["code-review"]        # skill names/globs offered to this agent
max_steps: 30
permission_mode: manual        # optional override
permissions:                   # optional agent-level rules
  - {permission: bash, pattern: "npm test*", action: allow}
memory: true
---
```

Add an agent by adding a folder — no code changes. Override a bundled agent by creating
the same folder under `~/.auoreo/agents/` or `<project>/.auoreo/agents/`.
