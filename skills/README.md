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

## Skills hub (taps, quarantine, lockfile)

`auoreo skills taps add <url>` registers a tap — an HTTP(S) JSON index
(`{"skills": [{"name", "description", "version", "url", "sha256"}]}`, `url` = raw `SKILL.md`
or a `.tar.gz`/`.zip` of the folder) or a git repo (`git+https://…`, `….git`; skills under
`skills/<name>/`). Indexes are cached for `g6.skills.index_ttl_s` (1 h).

```bash
auoreo skills search lint          # every tap
auoreo skills install tidy-imports # → ~/.auoreo/skills/.quarantine/tidy-imports (invisible to agents)
auoreo skills approve tidy-imports # re-hash vs skills.lock + lint, then → ~/.auoreo/skills/
auoreo skills list | verify | remove <name> | sources
```

`~/.auoreo/skills.lock` pins each install (`tap, version, sha256, status, files,
has_scripts, lint`); a pinned index `sha256` must match the download; archives are refused on
`..`/absolute paths, symlinks or more than `g6.skills.max_skill_bytes`. Every action goes to
`~/.auoreo/logs/skills_hub.jsonl`. Skills offered by connected MCP servers as
`skill://<name>` or `<scheme>://skills/<name>` resources are listed as `mcp:<server>` sources
(`auoreo skills sources`, `GET /api/skills/sources`); `POST /api/skills/hub/{name}/approve`
approves from the API.

## Preferences (PREFS.md) are proposed, never written

`memory` with `target: prefs` queues a proposal (`<home>/memory/.prefs_proposals.json`);
the user approves or denies it (`GET /api/memory/prefs/proposals?status=pending`,
`POST …/{id}/approve|deny`, or Memory → Curator in the UI). Approval emits
`prefs.approved` and writes `- <pref> <!-- since <date> · approval evt:<seq> · <id> -->`;
approved preferences join the per-turn memory block.
