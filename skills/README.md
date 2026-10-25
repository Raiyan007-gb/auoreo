# skills/

Agent Skills: `skills/<name>/SKILL.md` with `name` + `description` frontmatter, plus any
supporting files. Only the frontmatter is loaded at startup (it goes into the prompt's
skills index); the body is read when the agent calls the `skill` tool. Compatible with
`.claude/skills` — add that folder to `extra_roots` in `settings.json` to reuse them.

## Writing skills from sessions

Skills are procedural memory. The `skills_writer` agent (or the `distill-skill` workflow:
the default agent summarizes the procedure, then `skills_writer` writes it) saves skills
with the `skill_manage` tool (`create | update | delete | lint`, permission `skills`,
asks by default). It only writes to the runtime root `~/.auoreo/skills/<name>/` — never
to the repo — and reloads the harness so the skill is listed from the next turn.

Every write is linted, and `auoreo skill lint [<name>]` runs the same checks over every
discovered skill:

- `name` is kebab-case and matches the folder;
- `description` is 20–500 characters and says *when* to use the skill (it is all the model
  sees before loading it);
- the body has at least 50 characters;
- nothing looks like a secret (token prefixes from `core.redaction.PREFIXES`, private keys).

## Memory curation is deterministic

Facts in long-term memory are curated by a deterministic pass — no LLM — that runs inside
the server (hourly check; at most every `memory.curator_interval_hours`, only after
`memory.curator_min_idle_hours` without session activity) or on demand with
`auoreo curator run [--dry-run] [--force]`. It archives unused facts after
`curator_archive_after_days`, reports stale ones after `curator_stale_after_days`, merges
near-duplicates, never touches pinned facts and never deletes; every change goes to
`~/.auoreo/memory/.curator_ledger.jsonl` and each pass writes
`~/.auoreo/logs/curator/<stamp>/REPORT.md` (`auoreo curator report`). The `/curator` slash
command only asks the agent to review memory with `knowledge_search` and summarize.
