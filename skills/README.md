# skills/

Agent Skills: `skills/<name>/SKILL.md` with `name` + `description` frontmatter, plus any
supporting files. Only the frontmatter is loaded at startup (it goes into the prompt's
skills index); the body is read when the agent calls the `skill` tool. Compatible with
`.claude/skills` — add that folder to `extra_roots` in `settings.json` to reuse them.
