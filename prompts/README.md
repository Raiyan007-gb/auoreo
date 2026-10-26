# prompts/

Each `*.md` file here is one **system-prompt section**. Sections are assembled in
ascending `order`. Frontmatter:

```yaml
---
name: identity        # must match the file name
order: -1000          # lower comes first; ties sort by name
tier: stable          # stable = byte-stable for the whole session (cache-safe)
                      # context = re-rendered every turn as a user-role snapshot
complete: false       # true = this section IS the system prompt (shadows all others and the
                      # family overlay); at most one such section may exist
---
```

Template variables use strict `{{var}}` syntax; an unknown or malformed variable is a
load error. Available: `agent_name, agent_description, agent_instructions, tools_list,
skills_index, cwd, model, date, permission_mode, memory, os`.

Bundled order: identity (-1000) → safety (-900) → runtime (100, context) → plan (500) →
team (600) → tools (1000) → skills (1500) → agent (5000) → deliverable (9000) →
structured_output (9900).

`families/*.md` are appended for models whose id matches one of their `match` regexes
(the family with an empty `match` list is the fallback): anthropic, openai (gpt/codex),
gemini, kimi, default. A family with `mode: <permission mode>` (e.g. `plan_reminder`) is
never picked by model id: its text joins the per-turn context block while the session is in
that mode, so toggling plan mode never breaks the cached system prompt.

`variants.yaml` shapes request parameters per `(provider, model_regex)` and holds
`toolOrder` (must contain `<unlisted-tools>` exactly once).

Override any section by putting a file with the same name in `~/.auoreo/prompts/` or
`<project>/.auoreo/prompts/`.
