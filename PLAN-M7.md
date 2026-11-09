# M7 plan: token economy, SSH/cloud, specialized agents and bots, orchestration

## Context

AUOREO's roadmap (M3–M6) is fully shipped and was drift-corrected on 2026-11-04. The owner
wants the next milestone drawn from what the field shipped in 2026 and from what the harness
still lacks, and wants it planned and executed with the EGM plugin: one design document per
workstream under `docs/designs/`, reviewed by the Goldfish before code, then built test-first.
No live hosts, cloud CLIs, X credentials or bot tokens are assumed: every external surface gets a
fake and the unit/integration suites must pass on Windows and Linux.

### What the survey found (sources at the end)

| Product | What it does that AUOREO does not |
|---|---|
| Orca (stablyai, desktop) | one git worktree per agent, diff review with annotations fed back to the agent, agents on a remote machine over SSH with auto-reconnect and port forwarding, a CLI agents use to spin up worktrees and post progress notes |
| Traycer | a phase board: intent → phases → file-level plan per phase → handoff to any agent → verification (acceptance checks, tests, diff reasoning) with discrepancies fed back |
| Claude Code 2026 | background agents, Agent View dashboard, `worktree.baseRef`, Remote Control, sub-agents spawning sub-agents, plugins as versioned bundles |
| Codex | OS-enforced sandbox (seatbelt, bwrap+seccomp), cloud tasks in isolated containers, `codex exec`/`codex cloud` |
| DeepSeek dsh-superagent (2026-10-31) | a long-running Router that watches group chats and GitHub, dispatches isolated Workers, comes back with PRs unprompted; everything-is-a-plugin harness |
| Grok Bot (2026-08) | a persistent computer with memory and scheduled routines; X connector that can search posts, read mentions and trends but not post; mention-triggered replies |
| Token economics | prompt caching cuts cost 45–80% in a 500-session study; the KV-cache hit rate is the production metric; stable prefixes, no timestamps in the prefix, stable JSON key order, never remove tools mid-session, file system as reversible context, todo recitation at the end of context, observation masking of old tool outputs, model routing for trivial calls |
| SSH tooling | agent-on-server with credentials kept local, saved connection profiles, SSH multiplexing CLIs for agents, tunnels and phone monitoring |

### What AUOREO already has (verified by three code inventories; build on these)

- **Context economy**: compaction with prune/summarize/restore/idle/partial (`src/auoreo/runtime/compaction*.py`), deferred tools + BM25 `tool_search` (87 of 133 tools deferred), Anthropic cache breakpoints on system, last tool and last user message (`gateway/pipeline.py::_anthropic_cache`), stable/context prompt tiers, `toolOrder`, aux/small-model chain (`gateway/auxiliary.py::resolve_aux_model`), opt-in bandit over model × effort × top_k (`policy/`), usage ledger with cached/cache-write tokens (`core/usage.py`), per-key daily budgets and key pools, per-turn `TurnBudget`, tool-result spill files (`tools/stages.py::prune`), web context gauge and token badge.
- **Remote**: `backends/environment/ssh` (asyncssh, key only from the secret store, known_hosts required, SFTP methods unexposed, agent and `~/.ssh/config` disabled), named environments (`settings.environments`), `use_environment` tool, `EnvironmentPool` routing with `environment.exec` audit (cmd_hash only), exec-server remote plane + UDS daemon + HMAC bridge (`src/auoreo/remote/`), tunnels (cloudflared, tailscale), secret store with rotate/grace.
- **Agents and bots**: 13 bundled agents (`AgentManifest`, `extra="forbid"`, no persona fields), Telegram/Discord/email connectors with pairing, allowlists and streaming (`plugins/`, `src/auoreo/plugins/connectors.py::InboundRouter`, one fixed agent per connector), `web_search` over a 7-backend chain, citations only by prompt (`skills/research`), consent-first suggestions and goals, jobs/cron, `delegate_task` with budgets, depth ceilings and fork modes, kanban + dispatcher, blackboard, sequential workflows with loops, `/review` subtasks in detached worktrees, `verify_plan`/`exit_plan_mode`, `new-feature` worktrees.

### Cache leaks found in the current request shaping (fix first; measured wins)

1. The third Anthropic breakpoint sits on the last `role == user` message; during a tool loop that is the turn's opening message, so the growing tool tail is never cached and the fourth allowed breakpoint is unused.
2. The per-turn context block is spliced onto the current user message at build time and not persisted, so the cached prefix diverges at that message on the next turn.
3. Loading a deferred tool changes the tools array (first in cache order) and misses the whole prefix.
4. S2 pruning rewrites old messages in place (4096-token hysteresis is the only mitigation).
5. `gateway.price()` prices cached tokens at full input price; `turn.summary`, ledger aggregates, Usage charts and insights omit cached tokens.
6. Direct tool descriptions are sent twice (`{{tools_list}}` and the schemas).

## Decisions from the Goldfish review (four fresh-context lenses: scope, architecture, observability, readiness)

### Binding constraints for every workstream

- **One schema revision, last.** There is no downgrade path (`src/auoreo/cli/commands/db.py`, `Database.init` refuses newer DBs). M7 gets at most one additive, nullable Alembic revision (`0009`, `SCHEMA_VERSION = 9`) landing as the final step; earlier pieces keep state in JSON columns, structured kanban comments (`__attach__`/`__link__` pattern), `UsageRow.tags`, or files under `~/.auoreo/`.
- **New knobs in a tolerant model.** `AgentManifest`, `WorkflowStep`, `SshEnvironment`, `GatewayTuning`, `AuxSettings` and others are `extra="forbid"`; a new key breaks the previous build on rollback. All M7 settings go in `m7: M7Settings` (`extra="ignore"`, precedent `i3`/`g6`/`teams2`); new manifest fields get a tolerant reader (accept + warn) before any definition uses them.
- **Kill switch per feature**, off by default until measured (precedent `bandit.enabled`).
- **Metrics allowlist is leaking**: `core/observability.py` drops any `obs.count` name not in `METRICS`/`HELP`; the three compaction counters are lost today. First task of W1: a test that every `obs.count("…")` literal is allowlisted, plus the three fixes.
- **Event severity is suffix-based** (`core/event_levels.py`); new event names use a known suffix or join `_EXACT`.
- **Files ≤ 500 lines**: `runner.py` is at the cap; new logic lands in `runtime/request_layout.py`, `runtime/masking.py`, `gateway/caching.py`, `policy/route.py`.
- **Context7 before code** against LiteLLM 1.106 (`cost_per_token` cache kwargs, tool-message `cache_control`, `prompt_cache_key`) and asyncssh 2.21 (`tunnel`, `keepalive_interval`, `sock=`).

### What survives, per workstream

**W1 Token economy.** Breakpoint on the last message regardless of role (fallback walk, never a thinking block or empty content) and the fourth breakpoint on the previous turn's last message, old rule as the flag-off path. One request-layout module that keeps the per-turn context block out of the cached prefix with a byte-identical-prefix test across steps and turns. Cache-aware pricing (widen `PriceFn`, fake gateway, ledger, `_spend`; one-off `auoreo db recompute-costs`). Cached and cache-write sums in `turn.summary`, ledger aggregates, `/metrics`, the Usage page; cache-hit ratio replaces the bandit's binary signal; `cache.prefix_hash`/`tools.hash` on the `usage` event and a `cache.prefix_changed` warning. Then, only after measurement and behind flags: batched observation masking behind an economic gate with originals spilled first, repeated-read dedupe, a rule route that goes through the bandit's accounting, OpenAI `prompt_cache_key`.

**W2 SSH and cloud.** Decision item first: SSH bypasses the egress proxy today, so either route the socket through the proxy (`CONNECT host:22`, asyncssh `sock=`) or declare a backend egress class with an `ssh:<host>` permission (`ask`) and a `net{mode, host, port}` audit; the permission path is the M7 default. Host inventory as a folder kind `hosts/<name>.yaml` (`HostManifest`) with runtime state under `<home>/ssh/`, web and CLI CRUD writing `~/.auoreo/hosts/` (precedent: the bundled-MCP enable endpoint), wire `schema/hosts.py` without key material. `auoreo ssh add|list|test|keygen|import`; keys generated into the secret store, public key and fingerprint in the inventory, `#prev` grace on rotation; CLI-only trust-on-first-use with audit, a changed key is never a prompt. `keepalive` and `tunnel` (jump host) fields; one `env_transfer` tool over the existing SFTP methods; `environment`/`host` in the policy request so a read-only host is a `.star` file; usage tagged `env=<name>`.

**W3 Agents and bots.** `memory_tenant` on the agent manifest (tolerant reader), memory service with a tenant parameter and a store pool; persona and style stay prompt content. Connector `routes` (regex → agent, operator allowlist) with sessions keyed by chat and agent, optional aux-model tie-break with a daily cap, `inbound.routed` audit. The "super-agent" is composition, not a Router session: a `kanban_create` suggestion action, an issue-to-kanban webhook recipe, an agent profile with explicit unattended permissions, dispatcher worktrees from W4, PRs only inside the accepted-suggestion session; fix the `is_unattended` hole for connector sessions. Structured `citations[]` in reply meta rendered per platform. Bluesky and Mastodon connector plugins (read-only polling with cursors and quota counters, posting only via accepted suggestions carrying a consent id); X deferred pending a paid-tier decision; Slack later via a plugin webhook provider. Telegram inbound voice through an additive `media` field and a `voice.transcribe` host request. Agent evals under `agents/<name>/evals/` with the fake gateway, no CI gate.

**W4 Orchestration.** Unblocker first: a per-session workspace root threaded through `ToolContext.cwd`, path checks, plans dir, policy context, sandbox profiles, hooks and project trust (the one `0009` column: `sessions.cwd`). Then `worktree: true` on `delegate_task` and the kanban dispatcher over the existing `create_worktree`/`worktrees.py`; workers commit on `auoreo/<task>` branches; merge-back is an interactive `git_merge` tool bound to an approved sha, refusing on a dirty checkout; conflicts become a kanban task; state derived from git; `worktree.*`, `merge.*`, `review.*` events. DAG workflows only as `needs`/`when` through the Starlark engine with sequential default and fleet-capped spawning; acceptance checks as a fixed vocabulary run through the tool pipeline. Task-graph UI deferred to M8; `task_ref` added to event payloads now.

### Not in M7 (reason)

- Agent forwarding: asyncssh can only forward an OS agent, which the backend deliberately excludes.
- Port-forward tool: a model-chosen network path around the egress proxy; static forwards as settings at most.
- tmux reattach: a cross-machine state machine; the exec-server already covers long-lived remote processes.
- Cloud instance auto-inventory: three CLIs and auth models for rows a human must still promote; a skill teaches the commands.
- Trust-on-first-use through the approval card: host keys are an operator decision, not a model-turn approval.
- `tool_call` wrapper for deferred tools: moves tool identity below the floors; measure tool-load churn first.
- Gemini explicit cached content: a billed, stateful resource needing its own lifecycle design.
- LLM intent router as the primary mechanism: one paid call per message; rules first, tie-break optional.
- Engine-level citation rewriting: breaks streamed text; structured meta instead.
- Automated conflict resolution and automatic merges: unauditable; conflicts block, humans approve merges.
- X connector: paid read tier; the plugin contract is documented, the plugin waits for an account decision.
- Visual task graph page: M8, after the backend slice is proven.

## Implementation steps

Conventions: every knob lives on `m7: M7Settings` (`src/auoreo/core/settings_m7.py`, sub-models
`token`, `ssh`, `agents`, `orch`; `extra="ignore"`; all flags default off). Each step names the
test written first. Gate bundles: **G-py** = `uv run ruff check . && uv run ruff format --check .
&& uv run mypy src && uv run pytest`; **G-harness** = `uv run auoreo harness validate`; **G-api** =
`scripts/check_openapi.py` → `cd web && npm run gen:types` → `bash scripts/gen_sdk.sh` →
`check_openapi.py --check`; **G-tool** = `scripts/verify_tool_catalog.py --write` then `--check`;
**G-web** = `npm run typecheck && npm run lint && npm run build && npm run e2e` (rebuild `web/dist`
before e2e); **G-hermetic** = `./scripts/run_tests.sh` on Linux plus `uv run pytest` on Windows.
Context7 checks, recorded as commit trailers: **C7-LiteLLM** (1.106: `cost_per_token` cache
kwargs, `cache_control` on `role: tool` messages, `prompt_cache_key` passthrough) and
**C7-asyncssh** (2.21: `tunnel`, `keepalive_interval`, `sock=`, `get_server_host_key`,
`SSHKey.get_fingerprint`). Execution order honours the owner's choice: each workstream's EGM design
doc is approved before its code starts.

### Wave 0: design docs and shared plumbing

0.1 Four EGM design documents, typed by the owner: `/egm:egm-brainstorm` with the hypothesis
    below → `/egm:egm-draft` → `/egm:egm-review .artifacts/egm/design_draft.md` (ask reviewers to
    check the AGENTS.md Invariants) → `/egm:egm-resolve`; approved docs land as
    `docs/designs/m7-token-economy.md`, `m7-ssh-hosts.md`, `m7-agents-bots.md`,
    `m7-orchestration.md` and are indexed in `docs/designs/README.md`.
    - Token economy: "Prompt-cache hit ratio on multi-step tool turns rises from near zero to above
      80% by persisting the per-turn context in the turn's user message and marking the tool tail,
      with no transcript or UI regression." Decision items: context carrier, breakpoint walk,
      price basis in tags, hit-ratio definition, cheap-route rerun, masking gate.
    - SSH hosts: "A folder-kind host inventory with harness-owned keys and known_hosts lets agents
      work on named hosts under per-host policies without SSH material leaving the secret store."
      Decision items: egress chokepoint (proxy CONNECT vs permission+audit; default the latter),
      key naming and `#prev` grace, CLI-only trust-on-first-use, `env_transfer` as the `net: none`
      exception.
    - Agents and bots: "Connector routing, per-agent memory tenants and structured citations turn
      one bot into several agents behind one chat without a router session or new wire contracts."
      Decision items: routes in plugin config, `(chat_id, agent)` session keying, tie-break cap,
      citations envelope, consent gating for posting, voice quota, unattended semantics.
    - Orchestration: "Per-session cwd plus git-derived worktree state lets child agents work in
      isolation and merge back only through an approved, sha-pinned `git_merge`, with the DAG
      optional." Decision items: cwd source order, trust inheritance, branch naming, merge refusal
      rules, skipped semantics, acceptance vocabulary, `task_ref` payload.
    Every doc carries a "Migration and rollback" section (flag-off path; no schema change except
    F.1) and the C7 checks as acceptance items.
0.2 `M7Settings` scaffold: create `src/auoreo/core/settings_m7.py`; add `m7` to `Settings` in
    `core/settings.py`. Test first `tests/unit/test_settings_m7.py` (flags off by default,
    unknown keys ignored, env JSON round-trip). G-py.
0.3 Metrics allowlist: test first `tests/unit/test_metrics_allowlist.py` walks every
    `obs.count("…")` literal under `src/auoreo` and asserts it is in `observability.METRICS`;
    add the three compaction counters to `METRICS`/`HELP` in `core/observability.py`. G-py.
0.4 Event levels: add `cache.prefix_changed` and `merge.conflicts` as `warn` to `_EXACT` in
    `core/event_levels.py`; other new names use known suffixes. G-py.

### Wave 1: measured wins and unblockers

**W1 token economy**
1.1 Telemetry first (`m7.token.telemetry`): create `gateway/caching.py` (`prefix_hashes`,
    `tools_hash`, `breakpoints`, `divergence`) and `runtime/request_layout.py` (`PrefixTracker`);
    merge `cache.prefix_hash`, `tools.hash`, `cache.breakpoints` into the `usage` event in
    `runtime/recovery.py::_Collect.feed`, set them on the `llm.call` span
    (`observability.traced_stream`), emit `cache.prefix_changed`; add `deferred`/`loaded` counts to
    `step.started`. Test first `tests/unit/test_cache_telemetry.py`: a two-step tool turn then a
    second turn on the fake gateway; with today's layout the divergence index equals the turn-1
    user message (pins the bug). G-py.
1.2 Tail breakpoints (`m7.token.cache_layout: legacy|tail`): in `gateway/pipeline.py` keep
    `_anthropic_cache` as the legacy path, add `_anthropic_cache_tail` (third mark on the last
    message via a walk that never marks a thinking block or `content: None`; fourth on the
    previous turn's last message via `ShapeContext.turn_start`); `gateway.py::_kwargs` passes
    layout, `turn_start`, `session_id`. Test first: pin the old rule under `cache_layout="legacy"`
    in `tests/unit/test_gateway_pipeline.py`, add the tail and thinking-block cases. C7-LiteLLM.
    G-py.
1.3 One context carrier (`m7.token.context_carrier: splice|persisted`): move `runner._request`
    into `request_layout.build()` (frees runner lines); persist the rendered `<context>` block as
    the first text part of the turn's user message at persist time (survives
    `sequence.repair`, `turn_index`, the fake gateway's `text_of`); patch
    `teams/fork.py::exact_history` and `_line`, `compaction_summarize` rendering, the messages
    API display, with `strip_context`/`has_context` helpers. Test first
    `tests/unit/test_request_layout.py`: prefix hashes of request k are a strict prefix of
    request k+1 within and across turns; `cache.prefix_changed` never fires. G-py, G-hermetic
    (`AUOREO_SNAPSHOT=update` only if a snapshot legitimately changes, never in CI).
1.4 Cache-aware pricing (`m7.token.cached_pricing`): widen `PriceFn` in `core/usage.py` to
    cached and cache-write tokens, pass them in `gateway.py::price` and `_spend`, update
    `gateway/fake.py::price`; tag priced rows `price:cached`; add `auoreo db recompute-costs
    [--days] [--dry-run]` in `cli/commands/db.py` (never at startup). Test first
    `tests/unit/test_usage_cached_pricing.py`. C7-LiteLLM. G-py.
1.5 Aggregates and the bandit signal: cached/cache-write sums in `runtime/budget.py`
    (`TurnBudget`, `turn.summary`), `core/usage.py` (`totals/summary/series`),
    `tokens_total{type=cached|cache_write}`, `schema/chat.py::UsageTotals`,
    `schema/insights.py::UsagePoint`, the Usage page and charts; `policy/bandit.py::signals_of`
    uses the ratio cached/prompt over main-turn rows. Tests first in `test_usage.py`,
    `test_bandit.py`, the budget tests. G-py, G-api, G-web.
1.6 OpenAI `prompt_cache_key = session_id` as `_CACHERS["openai"]` (`m7.token.openai_cache_key`).
    Test first in `test_gateway_pipeline.py`. C7-LiteLLM. G-py.
1.7 Repeated-read dedupe (`m7.token.read_dedupe`): `SessionState.read_dedupe` keyed
    (path, sha, offset, limit) per turn in `tools/context.py`; `tools/read/handler.py` returns a
    short "unchanged since your earlier read" notice. Test first `tests/unit/test_read_dedupe.py`.
    G-py.
1.8 Deterministic tool arrays: test first `tests/unit/test_tool_order_determinism.py`; sort
    runtime-registered tools by name in `harness/registry.py` only if it fails. G-py.

**W2 SSH and cloud**
2.1 Chokepoint (`m7.ssh.hosts`): permission `ssh` with pattern `<host>` (`ask`) authorized once
    per session and host in `tools/environments.py` before a remote exec, plus audit
    `net.ssh{mode, host, port}`; the proxy-CONNECT alternative stays a documented follow-up.
    Test first: extend `tests/unit/test_env_ssh.py` (denied without a rule, allowed with one,
    audit row present). G-py.
2.2 Host inventory kind: `harness/manifests_hosts.py::HostManifest` (name, host, port, user,
    `key_secret`, tags, jump, shell, policy, keepalive, `audit_commands`, public key,
    fingerprint; Windows reserved names rejected); register `hosts` in `harness/discovery.py`,
    `registry.py` (inert in untrusted project roots) and `loader.py`; `core/hosts.py` (write a
    definition, `<home>/ssh/` for known_hosts and state, derive the `SshEnvironment`);
    `backends/environment/ssh/impl.py` takes `keepalive_interval`, `tunnel` through the jump
    host, and key candidates `NAME` then `NAME#prev`; `cli/commands/ssh.py` with `add`, `list`,
    `keygen` (ed25519 into the secret store, public key and fingerprint into the yaml), `test`
    (CLI-only trust-on-first-use with audit `host.trust`; a changed key is an error), `import`
    (Host, HostName, User, Port, ProxyJump only; the backend keeps `config=None`). Tests first in
    `tests/unit/test_ssh_hosts.py` and `test_env_ssh.py` against the in-process asyncssh server
    plus a second server as jump host. C7-asyncssh. G-py, G-harness.
2.3 Per-host policy, audit and usage: add `environment` and `host` to the policy request
    (`tools/stages.py::policy_context`, `core/policy.py::REQ_FIELDS`); `environment.exec` audit
    gains `call_id`, `run_id`, `host_id` and an optional redacted `cmd` when the host sets
    `audit_commands: redacted`; usage tag `env=<name>` excluded from budget matching in
    `gateway/budgets.py`; example `policies/read-only-host.star` (disabled by default). Tests
    first `tests/unit/test_policy_host_fields.py` and additions to the ssh and usage tests. G-py,
    G-harness.

**W3 agents and bots**
3.1 Close the unattended hole: `suggestions/engine.py::is_unattended` also returns true for
    connector-driven sessions (`plugins/connectors.py::is_connector_session`). Test first in
    `tests/unit/test_suggestions.py`. G-py.
3.2 `memory_tenant` (`m7.agents.memory_tenants`): the only new `AgentManifest` field, tolerant
    reader in `loader.py::load_agent`; the runner uses `MemoryService.for_tenant` and provisions
    on first use. Test first `tests/unit/test_memory_agent_tenant.py`. G-py, G-harness.
3.3 Connector routes (`m7.agents.connector_routes`): `routes: [{match, agent}]` in the connector
    plugin config schemas, evaluated in `InboundRouter.handle` with the webhook filter leaf shape;
    sessions keyed `(chat_id, agent)` in a sibling file; audit `inbound.routed`. The aux-model
    tie-break with a daily cap is Wave 2. Test first `tests/unit/test_connector_routes.py`. G-py.
3.4 Structured citations (`m7.agents.citations`): `runtime/citations.py` collects
    `{tool_call_id, url, title}` from `web_search`/`web_fetch` results; `run.completed` and the
    connector reply meta carry `citations`; Telegram renders a "Sources:" tail. Test first
    `tests/unit/test_citations.py`. G-py.

**W4 orchestration**
4.1 Per-session workspace root (`m7.orch.session_cwd`): `SessionRuntime.cwd` set in
    `coordinator.runtime()` from `SessionRow.cwd` (after F.1) or `TeamMemberRow.cwd`; threaded
    through `runner._engine`/`run_turn` (plans path, policy context, `ToolContext.cwd`),
    `runtime/hooks.py`, `compaction_restore.py`, `teams/service.py::spawn(cwd=)`,
    `services.create_session(cwd=)`, ACP and the MCP server; tools already resolve against
    `ctx.cwd`. Trust: a worktree under `<cwd>/.auoreo/worktrees/` inherits the parent checkout's
    trust. Test first `tests/unit/test_session_cwd.py`: a child in a worktree executes there by
    default and cannot write the parent checkout without the outside-cwd approval. G-py,
    G-hermetic.
4.2 `worktree: true` (`m7.orch.worktrees`): `runtime/worktrees.py::task_worktree` (branch
    `auoreo/<slug>`); `tools/delegate_task` gains the flag and passes `cwd`; the kanban
    dispatcher gets `i3.kanban.worktree`, attaches path and branch to the task, workers commit
    on their branch; events `worktree.created|removed|gc` and `task_ref` on the spawn payloads.
    Test first `tests/unit/test_worktree_delegation.py` on a temporary git repo. G-py, G-tool,
    G-hermetic.

### Wave 2: expansions, each behind its flag and approved doc

- W1: cheap route rule in `policy/route.py` through the bandit's `begin/finish`, rerun on the
  default model when the cheap model emits a tool call (`tests/unit/test_route_rule.py`);
  observation masking in `runtime/masking.py` as a compaction source behind the economic gate,
  originals spilled first, placeholders by call id, `context.masked|unmasked` events
  (`tests/unit/test_masking.py`).
- W2: host CRUD API and page (`schema/hosts.py`, `api/routers/hosts.py`, `pages/Hosts.tsx`;
  G-api, G-web); `auoreo ssh rotate|deploy` (CLI/web only, audited); `tools/env_transfer` over
  the existing SFTP methods (permission `sftp`, `ask`, documented `net: none` exception,
  G-tool); static forwards as `HostManifest.forwards`; `skills/cloud-hosts/SKILL.md`.
- W3: super-agent composition (`kanban_create` suggestion action with per-source caps and
  `dedupe_key`, `webhooks/issue-to-kanban.yaml`, `jobs/triage.yaml`, `agents/triage` with explicit
  unattended permissions, dispatcher worktrees); Bluesky and Mastodon connector plugins with
  `httpx.MockTransport` tests, cursors, quota counters and consent-gated posting; Telegram inbound
  voice via an additive `media` field and a `voice.transcribe` host request gated by
  `consumes: [voice]` with the fake STT backend; opt-in citation URL checks through the proxy;
  `agents/<name>/evals/` with `scripts/eval_agents.py` and `auoreo eval agents`, no CI gate.
- W4: `tools/git_merge` (`ask`, interactive-only, refuses on sha mismatch or a dirty checkout,
  conflicts open a kanban task; `merge.*` and `review.*` events; G-tool); `worktree gc
  --reconcile`; DAG workflows only if cheap (`needs`/`when` with a tolerant reader, `when`
  through the Starlark engine, spawning through `TeamsService`, skipped semantics, acceptance
  vocabulary `check:tests|lint|files_exist`).

### Final step
F.1 The one Alembic revision `0009_m7_session_cwd.py` (nullable `sessions.cwd`,
    `SCHEMA_VERSION = 9`), `SessionRow.cwd`, `create_session(cwd=)` persists it. Test first: a v8
    file DB upgrades in place and keeps its rows; `uv run auoreo db upgrade` on a copied
    `state.db`. G-py, G-hermetic.

## Verification (fakes only, Windows and Linux)

- W1: `AUOREO_GATEWAY=fake uv run auoreo web --port 0`, run a tool turn then a plain turn; the
  Usage page shows cached and cache-write columns; `auoreo logs --level warn` shows
  `cache.prefix_changed` with `context_carrier=splice` and nothing with `persisted`. Unit
  evidence: `test_request_layout.py`, `test_cache_telemetry.py`, `test_gateway_pipeline.py`.
- W2: the in-process asyncssh server fixture (plus a second as jump host) backs `keygen`, `add`,
  `test`, rotation and the policy fields; the module skips cleanly without Git Bash on Windows.
- W3: `InboundRouter` driven directly with a fake send; connector plugins under
  `httpx.MockTransport`; STT through `backends/stt/fake`; suggestions accepted only through the
  API in tests.
- W4: temporary git repos in tests; `delegate_task(worktree=true)` on the fake gateway shows
  `auoreo/<slug>` in `git worktree list`; the cwd acceptance test proves isolation.
- Every wave ends with G-py, G-harness, G-api, G-tool `--check`, G-web and `uv run auoreo doctor`.
  Known pre-existing Windows-only failures stay excluded by name (the cua-driver script test, the
  ACP POSIX-cwd test, the PWA offline spec).

## Sources

- Traycer: https://traycer.ai/blog/build-with-intent-ship-with-confidence
- Orca: https://www.onorca.dev/ , https://betterstack.com/community/guides/ai/orca-ai/
- OpenCode: https://pinggy.io/amp/blog/best_open_source_cli_coding_agents
- Claude Code: https://code.claude.com/docs/en/whats-new.md
- Codex: https://deepstation.ai/blog/what-is-openai-codex-the-guide-to-ai-powered-coding-2026
- DeepSeek: https://dataconomy.com/2026/11/02/deepseek-releases-open-source-dsh-superagent-coding-agent/ , https://github.com/deepseek-ai/deepseek-harness
- Grok Bot: https://opentweet.io/blog/does-grok-post-to-x , https://techcrunch.com/2025/03/07/x-now-lets-you-query-grok-by-mentioning-it-in-replies
- Prompt caching study: https://arxiv.org/abs/2601.06007v1 ; TokenPilot: https://papers.cool/arxiv/2606.17016
- Context engineering (Manus): https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus
- SSH tooling: https://agentsroom.dev/features/ssh-connections , https://awesome.ecosyste.ms/projects/github.com%2Ffivif%2Fsshc
