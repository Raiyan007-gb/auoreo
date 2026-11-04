# Design documents

Approved design specifications for AUOREO, produced with the Elephant-Goldfish Model
(the `egm` Claude Code plugin: `/egm:egm-brainstorm` → `/egm:egm-draft` → `/egm:egm-review`
→ `/egm:egm-resolve`). Working files (drafts, review reports, resolution records) live in
`.artifacts/egm/<design>/`, which is git-ignored; a design moves here once it is reviewed and
approved. `/egm:egm-drift-report docs/designs` compares these documents with the code.

| Design | Status | Summary |
|---|---|---|
| [m7-token-economy](m7-token-economy.md) | Approved 2026-11-05 | W1a: tail cache breakpoint, cache aggregates, cache-aware pricing. Carrier, prefix tracker and recompute deferred to W1b. |
| [m7-ssh-hosts](m7-ssh-hosts.md) | Approved 2026-11-05 | First wave (hosts-a): `hosts/` inventory, `auoreo ssh add\|list\|test`, CLI trust-on-first-use, per-host `ssh` gate with a documented egress deviation. |
| [m7-agents-bots](m7-agents-bots.md) | Approved 2026-11-05 | Connector routes, per-agent memory tenants (fact store), citations built from tool events. |
| [m7-orchestration](m7-orchestration.md) | Approved 2026-11-05 | Per-session workspace root and worktree per task, no schema change; merge-back is a later wave. |
