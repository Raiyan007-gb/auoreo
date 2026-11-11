# M7 Wave 3: Specialized agents and bots (rule-based routing, per-agent memory, citations)

*   **Author:** Raiyan007-gb
*   **Status:** Approved (owner, 2026-11-05)
*   **Date:** 2026-11-05

**Terms used in this document.** *M7* is the seventh AUOREO milestone; its work is split into
waves W1 (token economy), W2 (SSH hosts), W3 (agents and bots, this document) and W4
(orchestration). *This wave* always means W3. *Later work* means items deferred beyond W3 and is
listed only under Future Improvements. *Goldfish* is the reviewer subagent set of the EGM design
workflow; the plan items "W3-1 … W3-8" are in the M7 plan (`docs/roadmap-m3-plus.md` and the M7
plan referenced under Additional Resources). A *connector* is a plugin that carries chat messages
in and out (Telegram, Discord, email, MQTT); *pairing* is the operator step that allowlists a chat
for a connector (`auoreo plugin pair`). A *memory tenant* is a named partition of `memory.db`
(`local` for the default; `team:<id>` for teams, provisioned by the teams subsystem). *Agent* means
a folder `agents/<name>/AGENT.md`; *bundled agents* are the ones shipped in `agents/`.

--------------------------------------------------------------------------------

## Overview

AUOREO already runs messaging bots: Telegram, Discord and email are out-of-process connector
plugins that pair chats, apply allowlists, stream replies and route each inbound message to one
session per chat through `InboundRouter` (`src/auoreo/plugins/connectors.py`). It ships 13 bundled
agents (researcher, coordinator, reviewer, ops, and others), a web search chain with seven
backends, and a web fetch tool.

The products surveyed for M7 (DeepSeek's dsh-superagent with its Router and Workers, Grok Bot
routines on X; links under Additional Resources) put several specialists behind one chat and show
the sources an answer used. AUOREO has the parts but they are not connected. This wave connects
three of them with small, flag-gated changes and no schema migration:

1.  **Rule-based routing**: an operator writes ordered rules in a connector's config; a message
    goes to the first matching agent, in a separate session per chat and agent.
2.  **Per-agent memory**: an agent can name a memory tenant; its facts, recall, transcript search
    and compaction summaries stay in that tenant.
3.  **Citations**: replies carry a structured list of the web pages the turn fetched, built from
    the turn's `tool.completed` events, and Telegram renders it as a "Sources:" list.

## Problem Statement

*   **One agent per connector.** A connector's config names a single `agent` (read at
    `plugins/host.py:451`); a Telegram bot cannot send research questions to the researcher and
    ops questions to the ops agent.
*   **One memory for every agent.** Agent memory is on or off (`AgentManifest.memory`); a support
    persona and a coding persona share facts. Memory tenants exist (`memory/service_i4.py`), but
    only the teams subsystem provisions them, and only the recall block reads them.
*   **Citations are prompt-only.** The research skill asks for sources, but nothing ties a cited
    URL to the tool call that fetched it, and connectors cannot render sources.

The "proactive work" ideas from the survey (connector sessions that accept suggestions, an
issue-to-pull-request recipe) are deferred; the first was verified to have no reachable code path
today (see Future Improvements and Open Question 2).

## Goals

*   Inbound connector messages route by operator rules (match on message fields, then agent) to a
    session per chat and agent; every routing decision is audited as `inbound.routed`.
*   Routes to an agent other than the connector's default are restricted to owner chats unless the
    operator opts a route out.
*   An agent can declare `memory_tenant`; agents with different tenants do not see each other's
    facts, recall, transcript search or compaction summaries.
*   Replies from a routed turn carry `citations[{tool_call_id, url, title}]` for the pages that
    turn fetched; the Telegram plugin renders them.

## Non-Goals

*   An LLM intent router as the primary mechanism (one paid call per message); an optional capped
    tie-break is later work.
*   A long-running Router session watching channels (a singleton that accrues context and spends
    per poll).
*   Connector sessions accepting suggestions, and the issue-to-kanban recipe (later work).
*   An X connector (X's read API is paid and the account decision is the owner's), Bluesky,
    Mastodon, Telegram voice input and agent evals (later work).
*   Rewriting streamed reply text in the engine.
*   Persona or style fields on the agent manifest (persona is the AGENT.md body).
*   Per-sender (user id) authorization: the connector protocol carries only a display name (see
    Open Question 3).
*   Isolating the shared memory files `MEMORY.md`, `USER.md`, `PREFS.md` (Open Question 1).
*   Any database migration (no Alembic revision, no `SCHEMA_VERSION` bump). Config and manifest
    changes are described explicitly below.

## Open Questions

Owner decisions; each has the default this design implements.

1.  **Shared memory files.** `MEMORY.md`, `USER.md`, `PREFS.md` live in `~/.auoreo/memory/` and are
    injected into every agent's context (`memory/service_i4.py:70-85`) and written by the `memory`
    tool (`tools/memory/handler.py:218`). *Default:* they stay shared by design (they describe the
    human, not an agent), CUJ 3 covers the fact store only, and the docs say so. *Alternative:* for
    tenanted agents, skip the file block and refuse `target: memory|user` writes.
2.  **Connector-session consent.** *Default:* drop from this wave. `SuggestionEngine.accept` has
    one production caller (`api/routers/suggestions.py:59`, the HTTP API, with no `session_id`),
    so the unattended check is never consulted in production. Revisit with an untrusted-origin
    flag set at session creation if a tool or chat command ever calls `accept`.
3.  **Per-sender restriction.** Pairing allowlists chats, not people, and the plugin protocol
    passes only a display name (`username or first_name`, `plugins/telegram/plugin.py:354`).
    *Default:* routes to non-default agents require the chat to be listed in the connector's
    existing `owners` setting (chat-level; operators should pair private chats for privileged
    agents). *Alternative:* extend the plugin protocol with a stable `sender_id` and add
    `senders: [...]` to routes.
4.  **Flags.** *Default:* keep `m7.agents.memory_tenants` and `m7.agents.citations`; remove the
    unused `m7.agents.connector_routes` from `settings_m7.py` because routing is already opt-in by
    the presence of `routes` in a connector's config.
5.  **Reply labelling.** *Default:* a routed reply from a non-default agent starts with
    `[<agent>] ` so users can tell which specialist answered. *Alternative:* no label.

## Critical User Journeys (CUJs)

### CUJ 1: One Telegram bot, several specialists

*   **Given**: the Telegram connector's `agent` is `default`, `owners` lists this chat, and its
    config has

    ```yaml
    routes:
      - match: {field: text, regex: "^/ops\\b"}
        agent: ops
      - match: {field: text, regex: "\\?$"}
        agent: researcher
    ```

*   **When**: a paired user sends "/ops disk usage on lab", then "what changed in LiteLLM 1.106?",
    then "thanks".
*   **Then**: the first message runs in a session for (chat, `ops`) with the text unchanged, the
    second in a session for (chat, `researcher`), the third in the chat's existing `default`
    session; the audit log has `inbound.routed` rows with `why` = `rule`, `rule`, `default`
    (`/ops` is not one of the fixed chat commands `/approve`, `/deny`, `/pending`, verified in
    `plugins/chat_approvals.py:32`, so it reaches routing).

### CUJ 2: A cited answer

*   **Given**: `m7.agents.citations` on, and the researcher route of CUJ 1.
*   **When**: the researcher calls `web_search` then `web_fetch` on two pages and answers.
*   **Then**: the reply meta holds `citations` with the `tool_call_id` of each fetch, and the
    Telegram message ends with a "Sources:" list of those two pages.

### CUJ 3: Separate memories

*   **Given**: `m7.agents.memory_tenants` on, `agents/support/AGENT.md` with
    `memory_tenant: support`, and the `default` agent without one.
*   **When**: the support agent stores a fact with the `memory` tool and a later turn asks
    `knowledge_search`.
*   **Then**: the fact is written to tenant `agent:support`; the default agent's recall,
    `knowledge_search` and `session_search` do not return it; another agent with
    `memory_tenant: support` does. `MEMORY.md`/`USER.md`/`PREFS.md` remain shared (Open Question 1).

### CUJ 4: A mistyped route does not strand the chat

*   **Given**: a route naming an agent that does not exist, or a regex that does not compile.
*   **When**: the operator saves the connector config, or (config edited on disk) a message
    arrives.
*   **Then**: saving is refused with the JSON pointer of the bad route; at runtime the bad route
    is skipped, the message goes to the next matching route or the default agent, and the audit
    row carries `why` = `invalid_route` or `unknown_agent`.

## Proposed Architecture

```text
connector.message ──► InboundRouter.handle
                        ├─ is_command (/approve /deny /pending)  → chat_approvals (unchanged)
                        ├─ plugins/routes.select(routes, payload) → (agent, why)
                        ├─ session_for(chat_id, agent): key "<chat_id>" for the default agent,
                        │  "<chat_id>:<agent>" otherwise, in the existing sessions.json
                        ├─ coordinator.submit(sid, text)         (agent bound at create_session)
                        ├─ coordinator.wait → result
                        ├─ citations.from_events(events of that run) → reply meta.citations
                        └─ audit.record("inbound.routed", ...)
runner turn ──► memory_for(spec): tenant service for agent:<slug>, or local
                  used by: recall, record_message, ctx.services["memory"] (memory/knowledge tools),
                  compaction summary indexing
```

**Routing.** `routes` is an ordered array in a connector's config. A route has `match` (a
`WebhookFilter`, the same filter model webhooks use, validated with `WebhookFilter.model_validate`
and evaluated with `automation.templating.filter_matches(filter, payload, base=None)`),
`agent` (a name in the agent registry) and `restrict` (`owners`, the default, or `chat`).
The payload is `{"text": <message text, first 1000 characters>, "user": <display name>,
"chat_id": <chat id>, "name": <delivery name the plugin sent, e.g. `telegram`>}`. Rules are tried
in order and the first match wins; regex is Python `re.search` (case-sensitive, unanchored unless
the pattern anchors); combinators `all`/`any`/`not` work; `in_file` is rejected (it reads from the
webhooks directory). A route with `restrict: owners` matches only when the chat is in the
connector's `owners` list (an unset or empty `owners` means such routes never match, which fails
closed); `restrict: chat` lets any paired chat use it. If no route matches, the connector's
existing `agent` key (which may be unset, meaning the `default` agent) applies, which keeps
today's behaviour for connectors without `routes`.

**Validation.** Config is validated at save time and again at host start: the JSON Schema of the
connector (`routes` item shape) plus `plugins/routes.validate(config, agent_names)`, which checks
that each regex compiles and is at most 200 characters, `agent` exists, and `in_file` is absent.
At message time the same checks run leniently: an invalid route is skipped with a warning (once
per config version) and `why` becomes `invalid_route` or `unknown_agent` for that decision if it
fell through to the default.

**Sessions.** Routed sessions are keys in the existing `<home>/plugins/<name>/sessions.json`:
the bare `chat_id` for the default agent (existing chats keep their history and older builds keep
working) and `"<chat_id>:<agent>"` for any other agent. The agent is bound when the session is
created (`create_session(agent=...)`); `coordinator.submit` is called without `agent=`, because a
per-submit agent would override the session's agent for that turn only
(`runner.py:177`, `agent = agent or rt.agent`). `session_for` serializes per plugin with an
`asyncio.Lock` (the host spawns one task per message) and reuses `_read_json`/`_write_json`.
Engine slash commands (`/model`, `/plan`, …) act in the routed session; a bare `/approve` still
answers the oldest pending request across sessions, so approval prompts sent to the chat name the
agent and session (`[<id>] <agent>: <tool> …`).

**Per-agent memory.** `AgentManifest` gains `memory_tenant: str | None = Field(default=None,
pattern=SLUG_RE, max_length=48)`. The field is always parsed: `load_agent` needs no flag access
and no change. The flag is read where the tenant is used, through a new
`MemoryService.for_agent(manifest)` (in `memory/service_i4.py`, or a new `memory/agent_tenant.py`
if that file would pass 500 lines): it returns `self` when `settings.m7.agents.memory_tenants` is
off or the agent has no `memory_tenant`; otherwise it provisions `agent:<slug>` once (`provision_tenant(..., kind="agent")`, audited as
`memory.tenant_provisioned`) and returns `for_tenant("agent:<slug>")`, built through the service's
worker-thread executor so no sqlite connection is opened on the event loop. The runner resolves
the service once per turn from `spec` and uses it for the recall block, both `record_message`
calls and `ctx.services["memory"]`, so tool writes (`tools/memory/handler.py`) and
`knowledge_search`/`session_search` land in the same tenant. Compaction summary indexing
(`runtime/compaction.py:413`) resolves the same service from the session's agent. The namespace
`agent:` is reserved (`local` and `team:<id>` are untouched), so an agent cannot name another
agent's or a team's tenant; `authorize_tenant` keeps its rule (own tenant or team tenant only).
`sanitize_agent_manifest` (`harness/trust.py`) also strips `memory_tenant`, so a project agent
from an untrusted root (which shadows a bundled one) cannot choose a tenant. Agent precedence over team: an agent with a tenant uses it even inside a team.

| Reader / writer | Agent without tenant | Agent with `memory_tenant: support` |
|---|---|---|
| Fact store (add/search/recall) | `local` | `agent:support` |
| `record_message`, transcript search, compaction summaries | `local` | `agent:support` |
| `MEMORY.md`, `USER.md`, `PREFS.md` | shared | shared (Open Question 1) |
| Team tenant `team:<id>` | team sessions only | agent tenant wins |

**Citations.** After `coordinator.wait`, `InboundRouter.handle` reads that run's `tool.completed`
events (`runtime/tool_round.py:74-84`: `run_id`, `call_id`, `tool`, `is_error`, `meta`) through
`EventStore.replay(after_seq, channels={"session:<sid>"})`, with `after_seq` taken from
`last_seq()` just before `submit` and paging at `REPLAY_LIMIT`. `plugins/citations.py` builds the
list: only non-error `web_fetch` results count, with `url = meta["url"]` (the final URL after
redirects, `tools/web_fetch/handler.py:121`); denied, robots-blocked and 4xx fetches are
`is_error` and skipped. A `web_search` hit (`meta["sources"]` of `{title, url}`) is added only when
its URL also appears in the reply text, and a fetched URL takes its title from a matching search
hit when there is one (otherwise `title` is `null`). URLs are deduplicated on the URL without
fragment, ordered by first appearance, capped at 8, and `tool_call_id` is the call that first
produced the URL. Events of delegated sub-sessions are not included (separate aggregates). Titles
are untrusted text: control characters are stripped, they are cut at 120 characters, and plugins
render them as plain text. The runner, `TurnResult` and the `run.completed` event are unchanged,
so there is no OpenAPI or SDK regeneration and `runner.py` does not grow. When `result` is `None`
(timeout) or the turn failed, `citations` is omitted. With the flag off, `citations` is omitted.

**Reply meta.** The existing call becomes `send(chat_id, reply, {**meta, "session_id": sid,
**extra, **cite})` where `cite` is `{"citations": [...]}` or empty and `extra` is the voice-note
result; a key clash is impossible because `voice_notes` only sets `media`.

**Audit.** `inbound.routed` is an audit row (`services.audit.record(actor="system",
action="inbound.routed", session_id=sid, decision="allow", reason=why, detail={"plugin", "chat_id",
"agent"})`), not an event, so `core/event_levels.py` needs no registration. One row is written per
inbound message while the connector has `routes`; connectors without `routes` write none. `why` is
one of `rule`, `default`, `invalid_route`, `unknown_agent`, `owners_only` (a matching route was
skipped because the chat is not an owner chat).

## Alternatives Considered

### Alternative A: LLM intent router for every message

*   **Pros**: no rules to write.
*   **Cons**: one paid call before every turn; a misroute is invisible without extra telemetry;
    and routing decides which agent's tools a message can reach, so a model misclassification is
    a privilege decision.
*   **Reason for Rejection**: deterministic operator rules are auditable and free; an optional
    capped tie-break can be added later without changing the rule model.

### Alternative B: Switch the agent inside one session per chat

*   **Pros**: one session file, one history, continuity across specialists.
*   **Cons**: permission grants, plan mode and loop-detector exemptions earned under one agent
    would carry over to another, and changing the system prompt mid-session invalidates the
    provider's prompt cache for the whole history.
*   **Reason for Rejection**: separate sessions keep each agent's grants and cache isolated; the
    cost is that a follow-up routed to another agent lacks the first agent's context (Risks).

### Alternative C: Engine-level citation formatting

*   **Pros**: consistent output on every connector.
*   **Cons**: the answer text is already streamed before it can be rewritten, and each platform
    escapes and limits text differently.
*   **Reason for Rejection**: structured metadata lets each plugin render in its own format.

### Alternative D: Citations on `run.completed` (a field on `TurnResult`)

*   **Pros**: the web UI could show them too.
*   **Cons**: `runner.py` is at its 500-line cap; `TurnResult` is built at about ten sites; the
    event may be typed in `schema/`, forcing OpenAPI and SDK regeneration.
*   **Reason for Rejection**: the same data already exists as `tool.completed` events; building
    from them after the run needs no engine change. A web citations view can reuse
    `plugins/citations.py` later.

### Alternative E: Memory isolation by reusing team tenants, a store per agent, or key prefixes

*   **Pros**: team tenants need no new code path; a store per agent isolates files too; prefixes
    avoid a second connection.
*   **Cons**: team tenants are provisioned and authorized by team membership, so an agent tenant
    would collide with team semantics; a separate store per agent multiplies sqlite connections
    and indexes and breaks cross-agent shared facts; prefixing keys inside `local` gives no
    structural guarantee.
*   **Reason for Rejection**: a new `agent:<slug>` tenant namespace reuses the existing tenant
    filter and `for_tenant` cache while keeping `team:` and `local` untouched.

--------------------------------------------------------------------------------

## Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Bad or hostile regex (ReDoS) on user text | event loop stalls on every message | patterns are operator-written, at most 200 characters, validated at save; matched text is cut to 1000 characters; Python `re` has no timeout, so the residual risk is an operator writing a catastrophic pattern (documented) |
| Routing reaches a powerful agent (ops has `bash`) from a group chat | any member of a paired chat can use its tools | `restrict: owners` default, fail-closed when `owners` is unset; `inbound.routed` audit; the agent's own permission rules and approvals still apply; per-sender rules are Open Question 3 |
| Display-name spoofing (`user` is a username or first name) | a route keyed on `user` is bypassable | docs state `user` is informational; authorization uses the chat id only |
| Route names a missing agent | run fails with `KeyError` and the chat gets nothing | validated at save and start; at runtime the route is skipped and the message falls through, with a `why` of `unknown_agent` |
| `sessions.json` grows with one session per chat and agent | slow reads; stale entries | bounded by the number of routed agents per chat; a missing session is recreated (existing `get_session` check); no automatic pruning in this wave |
| Duplicate sessions from concurrent first messages | two sessions for one key | per-plugin `asyncio.Lock` around read-modify-write |
| Follow-up lands in a different agent without context | user confusion | the `[<agent>] ` label (Open Question 5) and a documented limit; operators can write routes on patterns rather than words |
| Mistyped `memory_tenant` provisions a stray tenant | silent fragmentation | slug pattern and length limit; `memory.tenant_provisioned` audit row; no tenant management UI in this wave, so a stray tenant is listed through the audit log |
| Flag turned off after tenant facts were written | those facts become invisible | data is kept untouched; turning the flag on again restores them; there is no merge-back command in this wave |
| Downgrade to a pre-M7 build with `memory_tenant` in an agent file | `AgentManifest` is `extra="forbid"`, so that agent fails to load | documented: remove the key before rolling back; `auoreo harness validate` on the old build names the file |
| Citation list misleads (a fetched page that the answer did not use) | a source shown that was only consulted | the list is labelled "Sources consulted"; search hits count only when the answer text contains the URL; URL checks are later work |
| Untrusted text in titles or URLs | markup or control characters in chat | plain-text rendering, control characters stripped, length cut; no Markdown parse mode |
| Event replay fails or is slow after a long turn | no citations on that reply | best-effort: any exception is logged and the reply is sent without `citations` |
| Reply over a platform limit (Telegram 4096 characters) | send fails | the plugin trims the Sources block first, then the body, and keeps the existing splitting |

## Non-Functional Requirements

*   **Security**: routes are operator configuration (not user input); inbound text stays marked
    untrusted; a routed session runs with the routed agent's own permission rules and its
    approvals reach the chat only when `teams2.chat_approvals` is on (otherwise the session is
    non-interactive and unapproved tools are denied); each agent's tool grants come from its
    `AGENT.md`. `memory_tenant` is a namespaced tenant, never an arbitrary tenant id.
*   **Performance**: routing is one pass over a short list of compiled filters per message; the
    citation read is one bounded event query per reply; the tenant service is cached by
    `for_tenant`.
*   **Rollout & Rollback**: `m7.agents.memory_tenants` and `m7.agents.citations` default off
    (set through Settings or `auoreo config`, accessed as `settings.m7.agents.*`); routing turns on
    when a connector config has `routes` and off by removing it. No database change. Rollback:
    flip the flags off and remove `routes`; the default agent's sessions are the bare `chat_id`
    keys that older builds read; routed sessions are ignored by older builds; remove
    `memory_tenant` from agent files before downgrading (Risks).

--------------------------------------------------------------------------------

## Additional Resources

*   [DeepSeek dsh-superagent](https://dataconomy.com/2026/11/02/deepseek-releases-open-source-dsh-superagent-coding-agent/)
*   [Grok Bot and X](https://opentweet.io/blog/does-grok-post-to-x)
*   M7 plan: `docs/roadmap-m3-plus.md` (items W3-1 … W3-8)
*   Existing code: `src/auoreo/plugins/connectors.py`, `src/auoreo/memory/service_i4.py`,
    `src/auoreo/automation/templating.py`

## Future Improvements

*   Connector sessions that propose but cannot accept suggestions (an untrusted-origin flag set at
    session creation; Open Question 2), a `kanban_create` suggestion action and the
    issue-to-kanban-to-worker-PR recipe.
*   Bluesky and Mastodon connectors, Telegram inbound voice, citation URL checks, agent evals, a
    capped LLM tie-break router.
*   Per-sender routing (`sender_id` in the plugin protocol), a tenant selector for the memory API
    and CLI, `routes` for Discord and email, a web view of citations.

--------------------------------------------------------------------------------

## Detailed Implementation Plan

Work already present in the working tree and not repeated here: `core/settings_m7.py` (the
`m7.agents.*` flags), the `m7` field in `core/settings.py`, `core/env_catalog_data.py`,
`core/event_levels.py` and `tests/unit/test_settings_m7.py`. Unrelated to this design:
`.env.example` is untracked and conflicts with the AGENTS.md rule "no `.env.example`" (owner to
remove or justify). Phases each leave the build green.

### Target Files to Create/Modify

#### Phase 1: memory tenants

1.  `src/auoreo/harness/manifests.py`, `src/auoreo/harness/trust.py` (Modify): add
    `AgentManifest.memory_tenant`; strip it in `sanitize_agent_manifest`.
2.  `src/auoreo/memory/service_i4.py` (Modify) or `src/auoreo/memory/agent_tenant.py` (Create):
    `MemoryService.for_agent(manifest)` as described; a one-line `authorize_tenant` check that the
    agent namespace is never requestable cross-agent.
3.  `src/auoreo/runtime/runner.py` (Modify, net zero or negative lines): resolve the service once
    from `spec`; move the 8-line recall block into `src/auoreo/memory/recall.py` (Create) as
    `recall_block(...)`; use the resolved service for `record_message` and
    `ctx.services["memory"]`.
4.  `src/auoreo/runtime/compaction.py` (Modify; file has uncommitted metrics edits, keep them):
    index the summary through the session's tenant service.
5.  `src/auoreo/core/settings_m7.py` (Modify, uncommitted file): drop `connector_routes`
    (Open Question 4) and update the matching test.

#### Phase 2: citations

6.  `src/auoreo/plugins/citations.py` (Create): `from_events(events, reply_text) -> list[dict]`.
7.  `src/auoreo/plugins/connectors.py` (Modify): record `last_seq` before `submit`, read the
    run's events after `wait`, merge `citations` into the reply meta.
8.  `plugins/telegram/plugin.py` (Modify): `_send_final` appends "Sources:" (plain text,
    `tool_call_id` not shown, trimmed to fit 4096 characters).

#### Phase 3: routes

9.  `src/auoreo/plugins/routes.py` (Create): route model, `validate(config, agent_names)`,
    `select(routes, payload, owner, agents) -> (agent, why)`.
10. `src/auoreo/plugins/connectors.py` (Modify): route after `is_command`, composite session
    keys, per-plugin lock, no `agent=` on `submit`, `audit.record("inbound.routed", ...)`,
    `[agent] ` label.
11. `src/auoreo/plugins/host.py` (Modify; file is 498 lines, so move the `validate_config` call
    site to call `routes.validate` through a helper in `plugins/routes.py` without net growth):
    pass the registry's agent names; the existing `st.config.get("agent")` stays the default.
12. `plugins/telegram/plugin.yaml` (Modify): `routes` in `config_schema` (the schema has
    `additionalProperties: false`). Discord, email and MQTT schemas are unchanged, so `routes`
    is rejected there and `citations` meta is ignored by them.
13. `src/auoreo/plugins/chat_approvals.py` (Modify): approval prompts name the agent.

#### Phase 4: docs

14. `docs/` : a short section in the plugin docs for `routes`, `restrict`, `memory_tenant`, the
    shared-files note and the downgrade note.

### Tests (Create/Modify), mapped to CUJs

*   `tests/unit/test_connector_routes.py` (CUJ 1, 4): first-match order; default fallback and
    unset `agent`; combinators; invalid regex and unknown agent at validate and at runtime;
    `restrict: owners` with `owners` set, unset and not containing the chat; `/approve` bypasses
    routing; two agents in one chat make two sessions; `sessions.json` bare key for default and
    `"<chat>:<agent>"` otherwise, existing sessions reused; `inbound.routed` payload and `why`
    values; concurrent first messages create one session; `/ops` text reaches the agent
    unchanged.
*   `tests/unit/test_memory_agent_tenant.py` (CUJ 3): write through the `memory` tool and read
    through `knowledge_search`/recall in both directions; `record_message` and compaction land in
    the tenant; flag off returns `local`; untrusted project agent's tenant stripped; slug pattern; team
    session with tenanted agent; `harness validate` accepts `memory_tenant`.
*   `tests/unit/test_citations.py` (CUJ 2): search only, fetch only, both, empty, `is_error`
    (denied, robots, 4xx) skipped, cached search, duplicates and redirects, cap, title cleaning,
    flag off, `result` is `None`.
*   `tests/integration` or `tests/unit/test_telegram_plugin.py` (Modify): "Sources:" rendering and
    4096-character trimming under `httpx.MockTransport`.

### Verification Plan

*   `uv run ruff check . && uv run ruff format --check . && uv run mypy src`.
*   `uv run pytest` against the baseline commit `2404734` plus the existing working-tree
    changes; `./scripts/run_tests.sh` (hermetic); `tests/unit/test_portable_paths.py`.
*   `uv run auoreo harness validate`; `uv run python scripts/check_openapi.py --check` (expected
    unchanged: no wire change).
*   `AUOREO_GATEWAY=fake` live run: drive `InboundRouter` with a fake send through CUJ 1-4, using a
    fake search backend (`backends/search` fake) and `fake` gateway; manual check with a real
    Telegram test bot for Sources rendering.
*   Confirm file sizes: `runner.py` and `host.py` not above their current line counts.

## Wave 2 implementation notes (voice, Mastodon)

Built from the Wave 2 draft (items D and E, every open decision at its default); deviations:

*   **API details checked** (fetched 2026-11-11): Mastodon notification ids are strings "not
    guaranteed to be a number", so the cursor is sent as `min_id` and compared numerically only
    when every id is numeric; the rate-limit reset format is not documented, so
    `X-RateLimit-Remaining: 0` pauses polling for the documented five-minute window instead of
    "for the day" (the day stop is `daily_read_cap`). `Idempotency-Key` is kept for up to an
    hour. Telegram `getFile` returns `file_path`; the download is
    `/file/bot<token>/<file_path>`, at most 20 MB, as the draft assumed.
*   **Proxy gate.** The egress-proxy token is minted for plugins that declare `net:proxy` in
    `consumes` (the draft tied it to Mastodon by name). Without a proxy the Mastodon plugin
    makes no request (fail closed rather than direct).
*   **First poll** records the cursor and answers no backlog.
*   **Voice length** is measured host side after decoding, before the STT call, and charged
    first; the `voice.transcribed` event is not emitted for connector notes (it carries text).
*   **httpx logging.** httpx logs request URLs at INFO, which for Telegram contain the bot
    token; both connector plugins set the `httpx` logger to WARNING.
*   **Accept → post.** `auoreo.plugins.consent.post(services, suggestion_id)` is the entry the
    suggestion `post` action calls; that dispatch lives in `suggestions/actions.py` (built
    separately). Until it is wired, accepting a `post` suggestion records consent and posts
    nothing.
*   Reply mode proposes only a completed answer; a failed run proposes nothing.

--------------------------------------------------------------------------------

*Drafted with EGM (Elephant-Goldfish-Mouse design workflow)*
