# prompts/

Each `*.md` file here is one **system-prompt section**. Sections are assembled in
ascending `order`. Frontmatter:

```yaml
---
name: identity        # must match the file name
order: -1000          # lower comes first; ties sort by name
tier: stable          # stable = byte-stable for the whole session (cache-safe)
                      # context = re-rendered every turn as a user-role snapshot
---
```

Template variables use strict `{{var}}` syntax; an unknown or malformed variable is a
load error. Available: `agent_name, agent_description, agent_instructions, tools_list,
skills_index, cwd, model, date, permission_mode, memory, os`.

`families/*.md` are appended for models whose id matches one of their `match` regexes
(the family with an empty `match` list is the fallback). `variants.yaml` shapes request
parameters per `(provider, model_regex)`.

Override any section by putting a file with the same name in `~/.auoreo/prompts/` or
`<project>/.auoreo/prompts/`.
