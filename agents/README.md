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
memory_tenant: support         # optional (M7): this agent's own partition of the fact store
---
```

`memory_tenant` (a slug, at most 48 characters) takes effect when `m7.agents.memory_tenants`
is on: the agent's facts, recall, transcript search and compaction summaries then live in the
tenant `agent:<slug>` of `memory.db`, invisible to agents without that tenant and shared by
agents that name the same one. `MEMORY.md`, `USER.md` and `PREFS.md` stay shared by every
agent (they describe the human). Topic files (`memory/topics/*.md`, the `memory` tool's
`target=topic` and `memory_read` of a topic) have no tenant, so an agent with a tenant may
neither write nor read nor list them: the tools refuse and point it at `target=fact`, which
lands in its tenant. Turning the flag off hides the tenant's rows without
deleting them. An agent from an untrusted project root cannot choose a tenant. Remove the key
before running an older build: the manifest is strict there and the agent would not load.

Add an agent by adding a folder — no code changes. Override a bundled agent by creating
the same folder under `~/.auoreo/agents/` or `<project>/.auoreo/agents/`.
