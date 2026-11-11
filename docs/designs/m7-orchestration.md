# M7 W4: Orchestration (per-session workspace, worktree per task, approved merge-back)

*   **Author:** Raiyan007-gb
*   **Status:** Approved (owner, 2026-11-05)
*   **Date:** 2026-11-05

Terms used below: **M7** is the 2026 milestone that extends AUOREO's agent orchestration; **W4** is
its fourth work stream (this document); **Wave 2** is the later half of W4 (the merge tool, see
Non-Goals); the **kanban dispatcher** is `teams/dispatcher.py` (config `i3.kanban`, where `i3` is
the settings namespace of the earlier teams/kanban milestone), which on each tick (one polling
pass) claims ready tasks and spawns a worker session per task; the **coordinator** is
`runtime/coordinator.py` (it owns each session's in-memory runtime); an **attachment** is a kanban
task reference stored by `KanbanStore.attach`.

--------------------------------------------------------------------------------

## Overview

AUOREO can split work across agents: `delegate_task` spawns child sessions with budgets and depth
limits, the kanban dispatcher spawns one worker per ready task, workflows chain agents, and
`/review` runs in a detached git worktree (`runtime/subtask.py`). Feature worktrees also exist as a
CLI (`auoreo new-feature`, `auoreo worktree gc`, `runtime/worktrees.py`). What is missing is the
link between them: no running session can have its own workspace root, so none of these worktrees
is actually the place where a session's tools run.

Several 2026 agent tools (for example [Orca](https://www.onorca.dev/) and Claude Code's worktree
support) give each parallel agent its own git worktree on its own branch, and treat merging back as
a reviewed, explicit step. This design adopts that pattern for `delegate_task` and the kanban
dispatcher. It does not claim to be a security boundary against a hostile worker (see Risks).

## The Problem

*   **The workspace root is server-wide.** File tools, permission path checks, the plans directory,
    hooks, compaction restore and the policy/render context all resolve against `Services.cwd`
    (the server's launch directory). A `/review` child is told its worktree path in the prompt
    (`subtask.py`) while its tools still resolve against the parent checkout. `TeamMemberRow.cwd`
    is written by `teams/spawn_i3.py` (always the server cwd) and never read.
*   **Parallel workers share one checkout.** `delegate_task` and the dispatcher run every worker in
    the same directory. The "one writer at a time" rule is a prompt rule in the `coordinator` and
    `swarm_lead` agents only; dispatcher workers have no such guard.
*   **No merge-back.** Nothing merges a worker's branch, nothing refuses an unreviewed merge, and
    nothing records worktree creation as an event.

## Goals

*   A session can have its own workspace root. File tools (`read`, `edit`, `write`, `glob`, `grep`),
    their path checks, the plans directory, hooks, compaction restore and the policy/render
    context use it. Precise guarantee: a file tool call with a `path` outside the session root asks
    the `external_directory` permission (interactive) or is refused (non-interactive, unless a rule
    allows it); asserted by test.
*   Shell commands (`bash`) are **not** path-gated (the `external_directory` gate covers only tools
    with `pattern_arg: path`). Shell isolation holds only under an enforced sandbox backend
    (`workspace-write` confines writes to the session root plus an explicit writable git
    allowance, see Technical Plan). Where the sandbox is not enforced (reported by
    `NOT_ENFORCED_NOTE`, common on Windows) a worktree is a convenience and branch separation,
    not confinement. The tool description and `auoreo doctor` say so.
*   `delegate_task(worktree=true)` and the dispatcher (`m7.orch.worktrees`) create (or resume) a
    worktree on branch `auoreo/<slug>`, run the child there, and record path and branch.
*   A `worktree.created` event makes the work traceable (payload in the Technical Plan).
*   Workers can commit on their own branch, also under an enforced sandbox.

## Non-Goals

*   Automated conflict resolution or automatic merges.
*   Wave 2 (a separate design, not specified here): the `git_merge` tool (merges only an approved
    commit into a clean checkout; conflicts become a kanban task), `worktree gc --reconcile`,
    optional `needs`/`when` workflow edges.
*   A visual task graph (M8) and its `task_ref` event field: deferred until M8 needs it.
*   A worktree `removed`/`gc` event: gc is a CLI that does not open the event store; it prints its
    plan instead.
*   Hostile-worker containment on hosts without an enforced sandbox (see Risks).
*   Per-worktree MCP, plugin or LSP processes: those stay server-wide (inventory below).

## Open Questions

Owner decisions; each has a recommended default that the plan below already implements.

1.  **Worktree root.** In-repo `<repo>/.auoreo/worktrees/` (today's `new-feature` default; the
    parent's `glob`/`grep` then traverse the nested checkouts and the parent can write into them
    without a prompt) or outside the repo `<home>/worktrees/<repo-name>-<hash8>/`.
    *Recommended: outside the repo for orchestrator-created worktrees*, overridable by
    `m7.orch.worktree_root` (falling back to `g6.review.worktree_root` if set); `worktree gc` is run
    for each root.
2.  **Git write access under an enforced sandbox.** (a) a scoped writable allowance for the
    worktree's git metadata, (b) clone-per-task (`git clone --shared`), or (c) accept that workers
    cannot commit under an enforced sandbox and let the parent commit. *Recommended: (a).*
3.  **Schema change.** *Recommended: none in Wave 1* (the session cwd is read from the
    append-only `session.created` event, which already carries `cwd`). A nullable `sessions.cwd`
    column (Alembic revision 0009, `SCHEMA_VERSION` 8 to 9, edits to `core/db.py`) is the
    alternative; it makes any binary downgrade need `auoreo db restore`.
4.  **Who commits.** *Recommended: a prompt rule in the dispatcher worker prompt ("commit your
    work on the current branch before finishing"); no automatic commit.* The alternative is an
    auto-commit on worker completion.
5.  **Dependencies in a fresh worktree** (`.venv`, `node_modules` are ignored files and absent).
    *Recommended: do nothing in Wave 1; workers install what they need* (an optional `bootstrap`
    command is a follow-up).

## Critical User Journeys (CUJs)

### CUJ 1: Isolated delegation

*   **Given**: `m7.orch.session_cwd` and `m7.orch.worktrees` on (`worktrees` requires
    `session_cwd`), the server cwd is a clean git checkout.
*   **When**: the coordinator agent calls `delegate_task(goal="add a test", worktree=true)`.
*   **Then**: a worktree appears under the worktree root at `<flat-slug>` on branch
    `auoreo/<slug>` (`git worktree list`), the child's file tools and commands run there,
    `worktree.created` is emitted, and a child `write` to the parent checkout asks for
    `external_directory` (interactive) or is refused (non-interactive).

### CUJ 2: Kanban workers in parallel

*   **Given**: both M7 flags on, `i3.kanban.dispatch: true`, two ready tasks.
*   **When**: the dispatcher ticks.
*   **Then**: two workers run in two worktrees on two branches; each task carries an attachment
    whose `ref` is the worktree path and whose `note` is `branch=auoreo/<slug>`; workers commit on
    their branch. Re-dispatching a task resumes its branch and never resets it.

### CUJ 3: Crash recovery

*   **Given**: the server stopped while workers ran.
*   **When**: the server restarts, and later the user runs `auoreo worktree gc` (existing command,
    extended below).
*   **Then**: a re-dispatched task finds its attachment and gets its worktree back. gc removes
    clean worktrees that are merged into `HEAD` or `origin/HEAD` (after the 1 h `MERGED_GRACE_S`)
    or untouched for the stale threshold (`cli2.worktree_stale_days`), prunes missing folders, and
    **keeps any worktree whose task is claimed or running**; it lists what it kept.

## Technical Plan (Architecture)

### Layout and lifecycle

```text
delegate_task(worktree=true) | dispatcher._spawn
   └─► worktrees.task_worktree(top, root, slug)   (serialized per repo; wraps new_feature)
          └─► create_session(cwd=<worktree>)      (session.created carries cwd)
                 └─► coordinator.runtime(sid): SessionRuntime.cwd = validated cwd
                        └─► runner: ToolContext(cwd), plans dir, engine.context, render_context
create → work → done (worker commits; branch remains) → merge by hand or PR → gc removes
```

Lifecycle: the worktree is created on spawn, kept after the task completes (the branch holds the
result), merged by the user (by hand or via a PR; `git_merge` arrives in Wave 2), and removed by
`auoreo worktree gc` once merged and past the grace period. Nothing removes a worktree
automatically on task completion in Wave 1.

### cwd resolution

`SessionRuntime.cwd: Path | None` is resolved once in `coordinator.runtime()`: the `cwd` of the
session's `session.created` event, else `None` (the server cwd). `TeamMemberRow.cwd` is not a
source: `spawn_i3` writes it for audit only (now the child's real cwd). A single helper
`cwd_of(rt, services) -> Path` in the new module `runtime/cwd.py` returns `rt.cwd or services.cwd`;
every consumer calls it, and the module keeps `runner.py` and `coordinator.py` under 500 lines.

**Validation (fail closed).** A cwd is accepted only if it equals the server cwd or is a path
registered in `git worktree list` of the server repo and located under the configured worktree
root. It is checked at `create_session` (spawn) and again at `runtime()` load. If a stored cwd
fails (missing folder, unregistered, moved root) the run fails with code `CONFIG` ("worktree for
this session is missing"); it is never silently run in the parent checkout. The cwd is not an API
field; only `delegate_task` and the dispatcher set it.

### `task_worktree(top, root, slug)`

It wraps `worktrees.new_feature` (slug check `SLUG_RE`, `/` to `+` flattening, `auoreo/` prefix,
resume of an existing `<wt>/.git`, `_prepare_root`) and adds keywords rather than reimplementing:

*   `fetch=False`, `check_pr=False`: no network and no `gh` for orchestrator use;
*   base ref: `HEAD` of the server checkout (not `origin/HEAD`), so a child sees what the parent
    committed. Uncommitted parent changes are **not** carried over; the tool result and the
    child's first message say so (dirty parent: warn, do not fail);
*   `reset=False`: never `-B`. If `auoreo/<slug>` already exists without a worktree, it is checked
    out (`git worktree add <dest> <branch>`); if it is checked out in another worktree the spawn
    fails with a clear error. The CLI `new-feature` keeps `reset=True`;
*   slug: `k<board>-<task_id>` for kanban, `<name>-<4 hex>` for `delegate_task`, lowercased and
    validated by `SLUG_RE`; a name the rules change (or one over 48 characters) gets
    `-<8 hex of sha256(name)>` so two tasks never share a worktree, and a worktree whose
    recorded branch is not `auoreo/<slug>` is not resumed;
*   a per-repo `asyncio.Lock` serializes `git worktree add` (a `delegate_task` batch of up to 3
    would otherwise race on git's lock files);
*   a path-length guard (on Windows the full worktree path must stay under 200 characters) and the
    cap `m7.orch.max_worktrees` (default 8; over the cap the spawn fails, nothing is evicted).

### `delegate_task` change

New boolean `worktree` (default false) at the top level and inside `tasks[]` items of
`tools/delegate_task/tool.yaml` (it sets `additionalProperties: false` at both levels).

| Condition | Result |
| --- | --- |
| `m7.orch.worktrees` off | error `worktree is disabled (m7.orch.worktrees)`; no child starts |
| server cwd not in a git repo | error; no child starts (no silent fallback) |
| `mode` is `fork` or `exact` | allowed (history is copied, cwd differs); the child's first message states its cwd |
| parent repo dirty | allowed; warning in the tool result (see base ref) |
| spawn fails after the worktree was created | the new worktree is removed (`git worktree remove`; the branch is kept if it has commits); the error is returned |
| batch (`tasks[]`) with `worktree` | one worktree per task, created serially, all-or-nothing: if one fails, those created in this call are removed |

### Kanban dispatcher flow

`KanbanDispatcher._spawn` today: `claim`, `create_session`, `set_session`, `submit`. With
`m7.orch.worktrees` on the order is:

1.  `claim` (unchanged);
2.  `task_worktree(...)`; on failure unclaim the task, apply the existing cooldown guard and log;
    no session is created;
3.  `create_session(cwd=path, ...)` (new keyword: the session is born with its cwd);
4.  `kb.attach(task_id, ref=str(path), note="branch=auoreo/<slug>", by=name)`: the durable
    task-to-worktree mapping used on re-dispatch and by gc;
5.  `set_session`, `submit` (unchanged), then emit `worktree.created`.

On restart a re-dispatched task finds its attachment in step 4, and `task_worktree` resumes the
same folder and branch. The option `i3.kanban.worktree` does not exist and is not added:
`m7.orch.worktrees` covers the dispatcher (as `settings_m7.py` already documents).

### Sandbox, git directory and shell commands

A linked worktree keeps its git metadata in the parent repository: `<common>/worktrees/<name>`
(HEAD, index), `<common>/objects`, `<common>/refs/heads/auoreo/<slug>` and its `logs` twin. All are
outside the child's cwd, so under an enforced `workspace-write` sandbox `git commit` fails today (a
mount outside cwd needs `danger-full-access`, see `ExecSpec.outside_cwd`).

Design (recommended option 2a): when the session cwd is a registered worktree, `bash`'s `ExecSpec`
gets a new `git_allow` list, computed by the engine (never model-supplied) and limited to the
worktree's own admin dir and the `auoreo/` branch namespace with its ref log (writable), and
`<common>/objects` (read-only). The worker's git writes new objects to a private store
(`GIT_OBJECT_DIRECTORY=<admin>/objects`, the shared store as `GIT_ALTERNATE_OBJECT_DIRECTORIES`);
after each `bash` call, and before gc reads a branch, the orchestrator imports them into the
shared store (`runtime/worktree_git.import_objects`: `pack-objects` then `index-pack`, which
verifies every object hash). `ExecSpec.outside_cwd()` exempts exactly these paths and each mount
is audited as `sandbox.mount`. Everything else in the parent (working tree, objects, hooks
directory, `config`) stays unwritable. Residual risk, stated: the worker can move any branch in
the `auoreo/` namespace (a per-task namespace would need a different branch name scheme, since git
replaces a ref through a lock file beside it, and `auoreo/<slug>` cannot also be a folder); the
orchestrator therefore resumes an existing branch only when the task's first `worktree.created`
event recorded its base and the tip descends from it, and refuses an unrecorded one (a delegate
child never resumes). A sibling that plants a descendant of the same base is not detected. The
orchestrator never runs git with a
worktree as the repository (`worktree_git.tree_git`: git dirs from the server's records, no
fsmonitor, hooks or submodules). Without an enforced sandbox nothing is confined, as the Goals
state.

### Trust and discovery scope

Definitions (agents, tools, hooks, policies, MCP) and trust stay bound to the **server workspace**.
A worktree's own `.auoreo/` is **never** loaded and never inherits trust: discovery is
process-global (rooted at the server cwd) and `harness/trust.py` keys trust by exact workspace
path, so a worktree path is untrusted by construction. Hook scripts with relative paths resolve
against the definition root, not the session cwd (`hooks.py` script resolution stays on
`Services.cwd`). The hook process also runs in the server cwd: it is outside the sandbox, so a
bare `pytest`, `make` or `./x` must not pick up files a worker wrote; the session cwd reaches a
hook only as the payload's `cwd`. This prevents a worker from
committing a hook or tool handler on its branch that later runs as trusted. A test asserts that a
hook committed in a worktree never loads.

### cwd consumer inventory

| Consumer | Decision |
| --- | --- |
| `ToolContext(cwd=...)`, `engine.context["cwd"]`, plans dir (`runner.py`) | switched via `cwd_of` |
| `render_context` (`harness/orchestrator.py`) | switched (receives the cwd argument) |
| `hooks.py` payload `cwd` | switched; the process cwd and script resolution stay on the server cwd |
| `compaction_restore.py` (3 sites) | switched; `rt` passed in |
| `teams/service.py` plans path, `teams/spawn_i3.py` member `cwd` | switched (child cwd recorded) |
| `runtime/subtask.py` (`/review`) | switched: the review child runs with the worktree as session cwd (fixes prompt-only isolation) |
| `coordinator._session_start` payload `cwd` | switched |
| `commands.py`, `mcp_resources.py`, `cron_extras.py`, `cli/commands/ops.py` | deliberately global (server-level features) |
| MCP, plugin and LSP processes | deliberately global; a worktree child reaches them only as `inherit_mcp_toolsets` allows |
| `floors_secrets` | works on resolved absolute paths; a `secrets*` file in a worktree gets the same treatment as in the parent (false positives accepted) |

### Flags

`m7.orch.session_cwd` (runtime cwd plumbing) and `m7.orch.worktrees` (creation) default off.
`worktrees` requires `session_cwd`: settings validation rejects the combination `worktrees` on and
`session_cwd` off, which would reproduce the prompt-only behaviour. New fields:
`m7.orch.max_worktrees`, `m7.orch.worktree_root`.

### Event contract

*   `worktree.created`, emitted on the child session id: `{path, branch, base, resumed, task_id?,
    board?}`, level `info` in `core/event_levels.py`. As a new wire event it needs a `schema/`
    model and the openapi/SDK regeneration from AGENTS.md.
*   No `worktree.removed`, `worktree.gc` or `task_ref` in Wave 1.

## Alternatives Considered

### Alternative A: Clone per task (`git clone --shared`)

*   **Pros**: no shared git directory, so the sandbox needs no git allowance.
*   **Cons**: refs are separate, so branches must be fetched back before merge-back; a full clone
    costs repository-size disk per task.
*   **Reason for Rejection**: merge-back (Wave 2) works on one repository's refs; worktrees keep
    them in one place. Kept as Open Question 2(b).

### Alternative B: A per-call `cwd` argument on every tool

*   **Pros**: no session state to store or validate.
*   **Cons**: every tool and handler changes; the model chooses the value; hooks, compaction and
    permission checks still need a session value.
*   **Reason for Rejection**: it spreads the boundary over every tool and makes it model-controlled,
    instead of one resolver.

### Alternative C: One server process per worktree

*   **Pros**: no cwd-consumer changes; MCP servers and plugins are isolated too.
*   **Cons**: N servers, ports and databases, and no shared session tree for `delegate_task`.
*   **Reason for Rejection**: conflicts with the one-app, one-port invariant.

### Alternative D: A stored worktree state machine (created, working, merged, ...)

*   **Pros**: an explicit status per task.
*   **Cons**: drifts from git after crashes and manual git commands.
*   **Reason for Rejection**: derive state from git, as `plan_gc` does; only task to path/branch is
    recorded, in the kanban attachment.

### Alternative E: Run each worker in a container

*   **Pros**: confines shell commands and the git directory fully.
*   **Cons**: needs Docker; already available as an environment choice (`docs/environments.md`).
*   **Reason for Rejection**: not available everywhere git is. Containers still compose with this
    design.

### Alternative F: Keep passing the path in the prompt (today's `/review`)

*   **Pros**: no engine change.
*   **Cons**: tools resolve against the parent; isolation depends on model compliance.
*   **Reason for Rejection**: this is the status quo, not a design; this document replaces it,
    including for `/review` (inventory above).

--------------------------------------------------------------------------------

## Risks & Mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Trust escalation: a worker commits hooks, tools or policies in its worktree | agent-written code runs as trusted | worktree `.auoreo/` never loaded, trust never inherited, hook scripts resolve against the definition root; test |
| Shell escape: `bash` can `cd ..` or `git -C <parent>` (no path gate) | worker writes the parent checkout | stated in Goals; an enforced sandbox confines writes; `doctor` and the tool description warn when it is not enforced; test under a fake enforced backend |
| Git under the sandbox: git dir outside cwd | worker cannot commit | engine-computed `git_allow` mounts (option 2a); test: commit succeeds under the enforced fake, writing `<common>/config` is refused |
| Data loss from `git worktree add -B` | a re-dispatched task resets its branch and loses commits | the orchestrator path uses `reset=False`: resume or fail |
| Concurrent `git worktree add` | lock-file errors on a batch | per-repo `asyncio.Lock`; batch is all-or-nothing |
| gc removes a live worker's worktree (a fresh worktree with no commits looks merged; `MERGED_GRACE_S` is 1 h) | lost in-flight work | gc skips worktrees whose task is claimed or running and those of live sessions; dirty worktrees are already kept; test |
| Disk use and Windows handles | repo size times worktree count; open handles block removal | `max_worktrees` (default 8), shared object store, gc reports sizes; removal reports the blocking process |
| MAX_PATH on Windows | obscure spawn failure | path-length guard before `git worktree add`; short slugs |
| Partial spawn failure | orphan worktree or stuck claim | rollback per the behaviour table; dispatcher step 2 unclaims |
| Stored cwd missing or tampered | worker runs in the wrong tree | validated at spawn and at `runtime()` load, fail closed |
| Branches pile up | clutter | lifecycle (merge, then gc) |
| Binary downgrade | none in the recommended design (no schema change); with the optional column an older build raises `SchemaTooNewError` and will not start | restore a backup with `auoreo db restore` (writes since the backup are lost) |

## Non-Functional Requirements

*   **Rollout & Rollback**: `m7.orch.session_cwd` and `m7.orch.worktrees` default off. There is no
    schema change, so flags off is a complete rollback and older builds start normally (they
    ignore the `m7` settings key via `extra="ignore"`). Existing worktrees stay on disk until
    `auoreo worktree gc`. If Open Question 3 chooses the column, rolling back the binary needs
    `auoreo db restore`, because older builds refuse a newer schema version.
*   **Scale**: at most 3 concurrent `delegate_task` children (existing), dispatcher per-profile caps
    (existing), at most `max_worktrees` live worktrees.
*   **Portability**: worktree paths are flattened and checked against the Windows path rules
    (`tests/unit/test_portable_paths.py`); tests use temporary git repositories and run in the CI
    OS matrix (Linux and Windows).

--------------------------------------------------------------------------------

## Additional Resources

*   [Orca](https://www.onorca.dev/), [Claude Code](https://docs.claude.com/en/docs/claude-code),
    [Traycer phases](https://traycer.ai/blog/build-with-intent-ship-with-confidence) (staged,
    verified work; cited for the review step only), the M7 plan in `docs/designs` (Goldfish
    W4-1 to W4-6).

## Future Improvements

*   Wave 2: `git_merge` with sha-bound approval and a PR fallback (push the branch and open a PR
    instead of a local merge), `worktree gc --reconcile`, `needs`/`when` workflow edges with a fixed
    acceptance-check vocabulary (exit code, file exists, tests pass).
*   `task_ref` and `worktree.removed|gc` events when M8 or a gc service needs them; an optional
    dependency `bootstrap` command.

--------------------------------------------------------------------------------

## Detailed Implementation Plan

Phases in order; each phase ends green on `ruff`, `mypy`, `pytest`.

### Target Files to Create/Modify

**Phase 1: settings and events**

1.  `src/auoreo/core/settings_m7.py` (Modify): `max_worktrees: int = 8` (1..64) and
    `worktree_root: str | None` on `M7Orch`; validator: `worktrees` requires `session_cwd`.
2.  `src/auoreo/core/env_catalog_data.py` (Modify): catalog entries for the new keys (web only).
3.  `src/auoreo/core/event_levels.py` (Modify): register `worktree.created` at `info`.
4.  `src/auoreo/schema/` event model for `worktree.created` (Modify, in the existing event schema
    module), then `uv run python scripts/check_openapi.py`, `cd web && npm run gen:types`,
    `bash scripts/gen_sdk.sh`.

**Phase 2: runtime cwd (flagged)**

5.  `src/auoreo/runtime/cwd.py` (Create): `cwd_of(rt, services) -> Path`, `validate_session_cwd`.
6.  `src/auoreo/core/services.py` (Modify): `create_session(..., cwd: Path | None = None)`; the
    `session.created` payload carries the child cwd instead of always `self.cwd`.
7.  `src/auoreo/runtime/runner.py` (Modify): `SessionRuntime.cwd`; `ToolContext`, plans dir,
    `engine.context` and the `render_context` call use `cwd_of`.
8.  `src/auoreo/runtime/coordinator.py` (Modify): `runtime()` resolves and validates the cwd from
    the `session.created` event; `_session_start` payload.
9.  `src/auoreo/runtime/hooks.py` (Modify): payload `cwd` from the runtime cwd; the process cwd
    and script resolution stay on `Services.cwd`.
10. `src/auoreo/runtime/compaction_restore.py` (Modify): the three `services.cwd` sites take `rt`.
11. `src/auoreo/harness/orchestrator.py` (Modify only if `render_context` needs a new parameter;
    `request_layout.py` is created by the token-economy design and is **not** a dependency here).
12. `src/auoreo/teams/service.py`, `src/auoreo/teams/spawn_i3.py` (Modify): plans path and member
    `cwd` use the child's cwd.
13. `src/auoreo/runtime/subtask.py` (Modify): the `/review` child runs with the worktree as its
    session cwd.

**Phase 3: worktrees and sandbox**

14. `src/auoreo/runtime/worktrees.py` (Modify): `task_worktree`, `reset`/`check_pr` keywords on
    `new_feature`, per-repo lock, cap and path guard; `plan_gc` accepts `protected: set[Path]`
    (the CLI passes paths from kanban attachments of claimed or running tasks when the store is
    reachable).
15. `src/auoreo/backends/sandbox.py`, `src/auoreo/tools/context.py` (Modify): `git_allow` on
    `ExecSpec`, its computation for worktree sessions, the `outside_cwd` exemption and audit.
16. `src/auoreo/cli/commands/worktree.py` (Modify): `gc` lists protected worktrees; emits no event.

**Phase 4: delegation and dispatcher**

17. `tools/delegate_task/tool.yaml`, `tools/delegate_task/handler.py` (Modify): the `worktree`
    parameter and behaviour table; then `uv run python scripts/verify_tool_catalog.py --write`.
18. `src/auoreo/teams/dispatcher.py` (Modify): the `_spawn` sequence above.
19. `src/auoreo/core/db.py` and an Alembic revision are **not** touched in the recommended design
    (they are the work list if Open Question 3 chooses the column).

**Phase 5: tests (Create)**

20. `tests/unit/test_session_cwd.py`: cwd resolution; fail closed on a missing or unregistered cwd;
    a file-tool write to the parent asks (interactive) or is refused (non-interactive); `cd ..` in
    `bash` is **not** blocked without a sandbox and is blocked under the fake enforced backend; a
    commit succeeds under the enforced fake; a hook committed in a worktree never loads; flags
    off change nothing.
21. `tests/unit/test_worktree_delegation.py`: temporary git repo; every row of the behaviour table
    (flag off, not git, dirty, fork/exact, spawn-failure rollback, batch all-or-nothing, existing
    branch resumed not reset, concurrent batch).
22. `tests/unit/test_kanban_worktree.py`: `KanbanDispatcher.tick()` with two tasks; attachment
    `ref`/`note`; restart resumes the worktree; worktree failure unclaims; gc skips a running task.

### Verification Plan

*   **Static analysis & linting**: `uv run ruff check . && uv run ruff format --check . && uv run mypy src`.
*   **Unit tests**: `./scripts/run_tests.sh` (hermetic); the baseline is the full suite green on the
    commit before the change. Also `uv run auoreo harness validate`,
    `uv run python scripts/check_openapi.py --check` and
    `uv run python scripts/verify_tool_catalog.py --check`.
*   **Manual**: enable both flags (`auoreo config set m7.orch.session_cwd true`, then
    `m7.orch.worktrees true`); in a clean git repo run
    `AUOREO_GATEWAY=fake uv run auoreo run "/tool delegate_task {\"goal\":\"x\",\"worktree\":true}"`
    (PowerShell: pass the JSON from a here-string). Expected: `git worktree list` shows a second
    entry on `auoreo/<slug>` and a `worktree.created` event exists. The fake-gateway child makes no
    commits; commits are covered by the sandbox test.

--------------------------------------------------------------------------------

*Drafted with EGM*
