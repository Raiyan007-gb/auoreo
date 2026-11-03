# CLAUDE.md

The project guide for coding agents is AGENTS.md (one file for every agent tool):

@AGENTS.md

## EGM in Claude Code

The `egm` plugin is enabled for this repo at project scope in `.claude/settings.json`
(`enabledPlugins` + `extraKnownMarketplaces`), so Claude Code installs it when the folder is
opened and trusted; by hand: `claude plugin install egm@egm --scope project`. It adds seven
typed commands; "Design workflow (EGM)" in AGENTS.md says what each one is for.

| Type | When |
|---|---|
| `/egm:egm-brainstorm <idea>` | before designing a feature that needs decisions |
| `/egm:egm-draft` | to turn `concepts_brief.md` or notes into `design_draft.md` |
| `/egm:egm-review .artifacts/egm/design_draft.md` | when the draft is complete, before approving it |
| `/egm:egm-resolve` | to work through a review's findings and edit the draft |
| `/egm:egm-review-loop` | to re-run one reviewer group on a range |
| `/egm:egm-drift-report docs/designs` | weekly, or before planning the next milestone |
| `/egm:egm-bootstrap <subtree>` | only for an undocumented subtree |

They carry `disable-model-invocation`, so the Skill tool refuses them: when a flow needs
another EGM skill, read its SKILL.md from the plugin cache
(`~/.claude/plugins/cache/egm/egm/<version>/skills/<name>/SKILL.md`) and follow it in this
thread. Reviewers never see this file or AGENTS.md: pass the Invariants section as a review
criterion in the prompt.

EGM artifact directory: .artifacts/egm
