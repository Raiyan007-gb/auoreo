# M7 W1a: Token economy (cache breakpoint on the tool-loop tail, cache metrics, cache-aware pricing)

*   **Author:** Raiyan007-gb
*   **Status:** Approved (owner, 2026-11-05)
*   **Date:** 2026-11-05

--------------------------------------------------------------------------------

## Overview

AUOREO sends every model step as one LiteLLM request built by `AgentRunner._request`
(`src/auoreo/runtime/runner.py`) and shaped by the gateway pipeline
(`src/auoreo/gateway/pipeline.py`). For Anthropic models the pipeline adds prompt-cache
breakpoints so a repeated prefix is billed at the cache-read rate (about a tenth of the input
price; cache writes cost about 1.25 times the input price) and is served faster. The paper
"Don't Break the Cache" (linked under Additional Resources) reports 45-80% lower cost and
13-31% lower time-to-first-token on its own 500+ agent sessions and names the cache-hit ratio as
the main production metric. Those figures are the paper's workloads, not a forecast for AUOREO.

W1a (the first workstream of M7, AUOREO's seventh milestone; W2 onward are other M7 workstreams)
makes three changes: (1) one more cache breakpoint on the last message of the request, so the
steps of a tool loop read each other's prefix from the cache; (2) cached and cache-write tokens
recorded in the turn summary, the usage ledger, `/metrics` and the Usage page, with one hit-ratio
definition; (3) cost estimates that price cached tokens at their own rates. Nothing in AUOREO
reports a cache hit ratio today, so W1a also produces the baseline that decides the deferred
parts (see "Deferred to W1b").

## The Problem

Two defects in request shaping keep the cache cold on multi-step tool turns, which send the
longest prompts, and two defects in accounting hide and misprice that:

1.  **The tool-loop tail is never cached.** `_anthropic_cache` marks the last tool definition, the
    system message, and the last `role == "user"` message. During a multi-step tool loop the last
    user message is the turn's opening message, so every assistant/tool round appended after it is
    re-sent at full price on every step. Up to four breakpoints are allowed and three are used.
2.  **The per-turn context block breaks the prefix every turn.** `_request` splices
    `<context>...</context>` into the current turn's user message at request time and never
    persists it. On the next turn that message is sent without the block, so the cached prefix
    diverges at the previous turn's user message. **W1a does not fix this** (it needs a carrier
    change that is deferred to W1b); W1a measures its cost through the hit ratio of the first step
    of each turn.
3.  **Nothing measures the cache.** Ledger rows store `cached_tokens` and `cache_write_tokens`
    (`UsageLedger.record`, `src/auoreo/core/usage.py`), but `turn.summary`, `UsageLedger.totals`,
    `summary`, `series`, `tokens_total{type}` and the Usage page use only prompt and completion
    tokens. A cache regression is invisible.
4.  **Cached tokens are priced at the full input rate.** `Gateway.price` calls
    `litellm.cost_per_token(model, prompt_tokens, completion_tokens)` without the cache split
    (`gateway.py`), so estimated costs overstate cache reads and understate cache writes. Daily
    budgets and the `max_cost_usd` cap use the same estimate (see "Budget effect").

Token convention (verified): `Usage.prompt_tokens` includes the cached and the cache-write
tokens (`gateway/types.py` `Usage` docstrings; `usage_parse.parse_usage` caps `cached` at
`prompt`), and `litellm.cost_per_token` also expects the total in `prompt_tokens`. So a hit ratio
`cached / prompt` lies in [0, 1], and `tokens_total{type=prompt}` already contains the cached
tokens.

## Goals

*   With `m7.token.cache_layout = "tail"` and an Anthropic model, the last markable message of every
    request carries a breakpoint, in addition to the three legacy marks, never more than four in
    total. Asserted at the pipeline level (`tests/unit/test_gateway_pipeline.py`).
*   Cached and cache-write tokens appear in `turn.summary`, `UsageLedger` totals/summary/series,
    `tokens_total{type="cached"|"cache_write"}` and the Usage page, with `cache_hit_ratio` defined
    once (`core/usage.py`).
*   With `m7.token.cached_pricing = true`, `Gateway.price` uses the cache-read and cache-write
    rates; the fake gateway exposes the same keywords so the path is testable.
*   The request-shaping and pricing changes are reversible by two settings (`cache_layout`,
    `cached_pricing`). Reporting additions (aggregates, metrics, UI) are additive and forward-only,
    and already-written cost estimates are not rewritten. No data migration.

## Non-Goals

*   The persisted context carrier, a production prefix-change detector, the fourth breakpoint on
    the previous turn, historical re-pricing, the bandit reward change, the OpenAI cache key and
    `read_dedupe`: deferred to W1b (below).
*   Changing how deferred tools load (`tool_search` keeps appending schemas). A `tool_call`
    wrapper would move tool identity below the permission floors; see Alternative D.
*   Gemini explicit cached content (a billed, stateful resource with its own lifecycle).
*   Observation masking and the cheap-model route: M7 Wave 2, decided on the numbers W1a adds.
*   Any schema migration. No Alembic revision is part of this design.

## Critical User Journeys (CUJs)

### CUJ 1: A long tool turn is cached step to step

*   **Given**: an Anthropic model, `m7.token.cache_layout = "tail"`, a session whose turn makes
    ten tool calls.
*   **When**: the agent loop runs the ten steps.
*   **Then**: each request after the first carries a breakpoint on its last message; from step 2 on,
    the provider-reported `cached_tokens` of a step is about the previous step's prompt size, so
    the step ratio is about `P(k-1) / P(k)`, and `turn.summary.cache_hit_ratio` equals
    `sum(cached) / sum(prompt)` over the turn. There is no fixed threshold: the ratio depends on
    how much a tool result adds per step.

### CUJ 2: An operator sees the cache working or regressing

*   **Given**: W1a deployed, `cache_layout = "tail"`.
*   **When**: the operator opens the Usage page or `/metrics`.
*   **Then**: cached tokens, cache-write tokens and the cache-hit ratio per model are shown. A
    change that breaks the prefix (for example a timestamp in the system prompt) appears as a drop
    of the ratio and a rise of cache-write tokens. Pinpointing the diverging message is W1b.

### CUJ 3: Rolling back

*   **Given**: the two settings were on.
*   **When**: the owner sets `m7.token.cache_layout = "legacy"` and `m7.token.cached_pricing =
    false` (live apply, no restart).
*   **Then**: requests get the legacy marks again and new rows are priced as before. Rows and
    aggregates already written keep their values (their `cost_usd` stays the cache-aware estimate);
    the new columns of the Usage page and the extra `tokens_total` series simply stay at their
    recorded values. A downgrade to an older build ignores the unknown `m7` keys (`_Tolerant`,
    `extra="ignore"`) and loses nothing.

## Technical Plan (Architecture)

```text
runner.run_turn ──► AgentRunner._request  (unchanged in W1a)
                      ▼
gateway._kwargs ──► pipeline: unsupported_parts → normalize → apply_caching → remap
                                                     │ _CACHERS["anthropic"]
                                                     ├─ cache_layout = legacy: today's marks
                                                     └─ cache_layout = tail: + last message
                      ▼
LiteLLM stream ──► Usage(prompt, completion, cached, cache_write)   (already parsed)
                      │
recovery._Collect.feed ──► usage event (already carries cached/cache_write)
                      │        └─ StepOutput += cached, cache_write ─► TurnBudget.record
                      ▼
UsageLedger.record ──► price(model, prompt, completion, cached=, cache_write=)  [pricing.py]
                      ├─► UsageRow ─► totals / summary / series (+ cached, cache_write)
                      └─► usage_metrics.record_cache_usage ─► tokens_total{type=cached|cache_write}
Gateway._spend ──► the same price call ─► budgets
```

### Tail layout

Marks 1 to 3 keep today's rule: the last tool definition, the system message, the last
`role == "user"` message. Mark 4 goes on the last markable message of the outgoing list, found by
walking back from the end and stopping at index 1 (index 0 is the system message). A message is
not markable when its content is `None` or empty (an assistant message that carries only
`tool_calls`), or when its last content block is `thinking` or `redacted_thinking`. A
`role: "tool"` message is marked with a message-level `cache_control` key, not by converting its
string content to text blocks: LiteLLM copies that key onto the Anthropic `tool_result` block
(`litellm/litellm_core_utils/prompt_templates/factory.py:1761-1790`). If the last markable message
is already marked, no extra mark is added. The total never exceeds `MAX_BREAKPOINTS` (4); with no
tools the layout uses three marks. Anthropic looks back from each breakpoint (about 20 content
blocks), so marking every message is unnecessary; the legacy last-user mark stays as a second
anchor inside the turn for a step that adds more blocks than the lookback covers (many parallel
tool results). A request-only note appended on the last step (grace or step-limit note) is marked
like any last message; it is written once and never read, which costs one write premium on that
step.

### Cache-hit ratio

`cache_hit_ratio(cached: int, prompt: int) -> float` in `core/usage.py`: `cached / prompt`, `0.0`
when `prompt == 0`. Ledger aggregates apply it to rows with `outcome == "ok"`: `outcome` is set only
for main-turn calls (`UsageMeter`), so title, compaction, guardian, sampling and plugin rows (which
leave it `None`) are excluded without a new column. `TurnBudget` counts only calls made by
`model_step`, so it is main-turn only by construction.

### Price basis and budget effect

`Gateway.price(model, prompt_tokens, completion_tokens, *, cached_tokens=0, cache_write_tokens=0)`
calls `litellm.cost_per_token(..., cache_read_input_tokens=cached_tokens,
cache_creation_input_tokens=cache_write_tokens)` when `cached_pricing` is on (argument names
verified in the installed LiteLLM 1.106.3, `cost_calculator.py:399-401`; a run with
`claude-sonnet-4-5` and 1000 prompt tokens gave 0.003 uncached, 0.0003 all cached, 0.00375 all
written). The estimate therefore moves in both directions: it falls on warm turns and rises on
cold or write-heavy turns. `max_cost_usd`, `tree_cost` and daily budgets read the same estimate,
so they can fire earlier on cold turns and later on warm ones. Provider-reported costs
(`Usage.cost_usd`, for example OpenRouter) are used as before and are not re-priced. `_spend` and
the ledger price the same `Usage` with the same keywords so they agree.

### Flags

| Key (`m7.token.`) | Type, default | Gates | Effect of switching back |
|---|---|---|---|
| `cache_layout` | `legacy` or `tail`, `legacy` | the fourth mark in `_anthropic_cache` | the legacy code path runs unchanged |
| `cached_pricing` | bool, `false` | the cache keywords reaching `litellm.cost_per_token` | new rows priced as before; written rows keep their value |

`telemetry`, `context_carrier`, `openai_cache_key` and `read_dedupe` exist in
`core/settings_m7.py` and are unused by W1a; they stay inert until W1b (or are removed, see Open
Questions). Reporting additions have no flag.

## Deferred to W1b

Decided after W1a's hit ratios exist. Each item lists what the Goldfish review found, so W1b does
not start from the first draft.

*   **Persisted context carrier** (fixes Problem 2). Needs: a trade-off paragraph (the rendered
    context holds the date, up to 8 memory recalls, the permission-mode reminder, locale, tone and
    variables; persisting adds that block to every turn until compaction, leaves stale reminders,
    and copies memory and variable values into `MessageRow` and exports outside memory deletion and
    variable rotation); a comparison against status quo, an in-memory per-session carrier and a
    stable-subset carrier; a structural marker, not the text prefix `<context>`; the exact new
    statement order in `run_turn` (persist, `message.created`, `turn_msg`, `inject_mentions`,
    `memory.record_message`, recall, `render_context`, nudges), with `recall_query` (reads
    `rt.history[-7:-1]`) pinned by a test; the full consumer list of message content (`fork`,
    `compaction_plan.render_transcript/_parts_text/text_only`, `compaction_extras`,
    `compaction.prune_plan`, `sessions_ops._export_md`, `attachments.flatten`, the sessions, context,
    compaction and openai_compat routers, `nudges`, coordinator, plugins, approvals, memory
    recording, the web renderer); and a rollback statement that an older build shows the block and,
    for list content, sends a Python repr (`runner.py` splice).
*   **Prefix tracker and `cache.prefix_changed`.** The hit ratio already detects regressions. If
    built: hash the pre-`cache_control` messages and tools with
    `h_i = sha256(h_{i-1} + canonical_json(m_i, default=str))[:16]` (reuse `teams/fork.py:cache_key`
    conventions), carry the result back on a new `Usage` field populated in `LiteLLMGateway.stream`
    after `_kwargs` (the unshaped `LLMRequest` in `_Collect` never sees `cache_control`); make
    `FakeGateway` call `shape()`; key by (session, served deployment); observe only
    `purpose == "turn"` base requests; reset on compaction (`compaction.post.marks`), model switch,
    fallback and tools-hash change; bound the map (LRU or session close); hash images by reference;
    define `expected_index = len(prev) - 1`.
*   **Previous-turn breakpoint (`turn_start`).** `turn_user_idx` indexes `rt.history`, not the
    outgoing list after `repair()` and `prepare_messages()` (skill mentions, steer, wrap-up and
    continue notes, placeholders and compaction shift it). Resolve by identity after repair, or
    inside the cacher; test with a skill mention, a steer and an unanswered call.
*   **Historical re-pricing** (`auoreo db recompute-costs`). The `price:cached` tag is unworkable:
    the ledger sees only a float from `price`, `UsageRow.tags` is `String(512)` and truncates, and
    `Usage.tags` feeds budget tag ceilings. If built: the price function returns
    `(cost, cache_aware)`, the marker is a row-only field set when `estimated`, the command defines
    its bootstrap (server stopped or encrypted-DB bootstrap), `--days`, `--dry-run` output,
    `None` prices and idempotence, and ships `tests/unit/test_db_recompute_costs.py`.
*   **Bandit `cache_hit`** from binary to ratio: rescales a reward whose posteriors were learned on
    0/1 (`policy/bandit.py` `signals_of`); needs its own flag and a statement on existing state.
*   **`usage` event additions** (`cache.prefix_hash`, `tools.hash`, `cache.breakpoints`),
    `step.started{deferred, loaded}` counters, OpenAI `prompt_cache_key`, `read_dedupe`.

## Alternatives Considered

### Alternative A: Keep the layout and only measure (do nothing to the breakpoints)

*   **Pros**: no behaviour change; the ratio is a clean baseline.
*   **Cons**: Problem 1 stays; every tool-loop step after the first re-sends the whole tail at the
    input rate.
*   **Reason for Rejection**: the tail mark is one pure function behind a default-off flag, so
    the baseline can be taken first by shipping the metrics step before enabling the flag.

### Alternative B: Move the breakpoint from the last user message to the last message

*   **Pros**: three marks only; no fourth mark.
*   **Cons**: when one step adds more than about 20 blocks, the lookback from the tail finds no
    earlier write and the step is a full miss; the last-user mark is the fallback anchor.
*   **Reason for Rejection**: keeping the legacy mark costs one of four allowed breakpoints and
    removes that failure mode.

### Alternative C: Let the provider place the breakpoint (top-level `cache_control`)

*   **Pros**: no walk-back logic; LiteLLM passes a top-level `cache_control` through
    (`llms/anthropic/chat/transformation.py:1721`).
*   **Cons**: not evaluated for placement rules, cost and the other providers AUOREO routes
    through; it replaces the explicit marks that the tests pin.
*   **Reason for Rejection**: no measured behaviour in AUOREO; revisit with W1a's numbers.

### Alternative D: Load deferred tools through a generic `tool_call` wrapper

*   **Pros**: the tools array never changes mid-session.
*   **Cons**: floors, `pattern_arg`, sandbox and `net` checks key on the outer tool name, and
    strict schema validation is lost.
*   **Reason for Rejection**: the security cost is paid before any measured benefit; W1b adds
    deferred-tool counters to measure the churn instead.

--------------------------------------------------------------------------------

## Risks & Mitigations

| Risk | Impact | Mitigation | Detection |
|---|---|---|---|
| Cache-aware pricing raises estimates on cold or write-heavy turns | `max_cost_usd` and daily budgets fire earlier | `cached_pricing` defaults off; the effect is stated in the release note | Usage page cost per model before/after; `cost_usd_total` |
| The tail mark misses the cache (more than about 20 new blocks per step, TTL expired during an approval wait, prefix below the model's minimum cacheable length) | Hit ratio stays low; each step pays the 1.25x write premium on its tail | Legacy last-user mark kept as second anchor; the 1h TTL setting (`cache_ttl`) is unchanged and documented | `cache_write_tokens` rising with a low ratio on the Usage page |
| `cache_control` on a `tool` message is dropped for a provider route (Bedrock, Vertex, OpenRouter) | No tail benefit on that route | Pipeline test pins the message-level key; the family dispatch applies the tail layout only to the `anthropic` family | Ratio of steps >= 2 near zero for one route |
| New aggregates double count | Wrong totals | `prompt_tokens` already contains cached; `cached`/`cache_write` are documented as subsets in `metrics_text.HELP` and the UI | `test_observability.py` pins labels and HELP |
| Line-cap breach in `gateway.py` (499) or `observability.py` (500) | `test_architecture.py` fails | `price` body moves to `gateway/pricing.py`; cache metrics live in `core/usage_metrics.py`; `observability.py` is not edited | `wc -l` and `tests/unit/test_architecture.py` in each step's gate |
| A price function with the old three-argument signature receives keywords | `TypeError` swallowed by `UsageLedger.record`, cost silently `None` | Keywords passed only when non-zero; the `Gateway` Protocol and the two test lambdas get the keyword-defaulted signature | `test_usage.py` asserts a non-None cost for a cached row |

## Open Questions

*   **Split (owner decision).** This draft implements W1a only and lists the rest as W1b, as the
    Goldfish review recommends. Accept the split, or keep any W1b item in this change?
*   **Telemetry scope (owner decision).** W1a reports through existing `usage` fields, the turn
    summary, the ledger, `/metrics` and the UI. Per-request breakpoint and prefix-hash events
    need the W1b data path. Is that enough for the baseline?
*   **Unused flags (owner decision).** Remove `telemetry`, `context_carrier`, `openai_cache_key`
    and `read_dedupe` from `core/settings_m7.py` until W1b, or leave them inert?
*   **Baseline order (owner decision).** Ship steps 3 to 6 (metrics, no behaviour change) first and
    enable `cache_layout = "tail"` only after a week of baseline, or enable both at once?
*   **Provider limits.** The lookback window, the per-model minimum cacheable length and the TTL
    prices are asserted from Anthropic's prompt-caching documentation; step 2 must cite them with a
    Context7 `Docs:` trailer (`libraryID@version`, access date) in the commit body.

Settled from code, no longer open: `cost_per_token` takes `cache_read_input_tokens` and
`cache_creation_input_tokens` (LiteLLM 1.106.3, `cost_calculator.py:399-401`), with the total in
`prompt_tokens`; a `role: "tool"` message with a message-level `cache_control` becomes a
`tool_result` with `cache_control` (`factory.py:1761-1790`).

## Non-Functional Requirements

*   **Security & Privacy**: no message text is added to events, spans or metrics; only token
    counts. The new metric label values (`cached`, `cache_write`) come from a fixed set.
*   **Performance & Latency**: the tail walk is O(messages) per step on the already-built list; no
    hashing, no new in-memory state per session.
*   **Rollout & Rollback**: two default-off settings (table under Technical Plan); reporting
    additions are forward-only; no schema change.
*   **File-size invariant** (500 lines; `tests/unit/test_architecture.py`): `gateway.py` (499) must
    not grow, so the `price` body moves to the new `gateway/pricing.py` (about 40 lines) and
    `gateway.py` keeps a short delegate; `observability.py` (500) is not edited because the
    `type` label already exists in its allowlist, and the new counters are emitted from the new
    `core/usage_metrics.py` (about 25 lines); `runner.py` (500) is not touched; `pipeline.py` (341)
    grows by about 30 lines.

--------------------------------------------------------------------------------

## Additional Resources

*   [Don't Break the Cache: An Evaluation of Prompt Caching](https://arxiv.org/abs/2601.06007v1)
*   [Context Engineering for AI Agents: Lessons from Building Manus](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
*   `.artifacts/egm/2026-11-03_DRIFT_REPORT.md` (the Goldfish findings W1-1 to W1-8 of the M7 plan;
    the plan document itself is not in the repository, the owner to link it)
*   `.artifacts/egm/m7-token-economy/consolidated_review_report.md` (review of the first draft)

## Future Improvements

*   The W1b items above; observation masking behind an economic gate, the cheap-model route and
    deferred-tool churn reduction in M7 Wave 2, decided on W1a's numbers.

--------------------------------------------------------------------------------

## Detailed Implementation Plan

Definitions: a *step* below is one numbered section; steps run in the order given and each
depends only on earlier ones. Each step is test-first, leaves the tree green, and ends with its
gate: `uv run ruff check . && uv run ruff format --check . && uv run mypy src` (no new errors) and
the named tests. "Baseline" is `uv run pytest` run once on the unmodified branch and recorded in the
PR description; a step passes when it adds no new failure to that list.

### Target Files to Create/Modify

#### Step 1. Cache-aware price: `src/auoreo/gateway/pricing.py` (Create), `src/auoreo/gateway/gateway.py`, `src/auoreo/gateway/types.py`, `src/auoreo/gateway/fake.py` (Modify)

*   **Type / Scope**: pricing, behind `m7.token.cached_pricing`. Depends on nothing.
*   **Proposed Changes**:
    *   `pricing.price(cost_per_token, candidates, prompt, completion, *, cached, written,
        cache_aware) -> float | None`: the loop of today's `Gateway.price`, with the LiteLLM function
        injected (litellm is imported only in `gateway.py`), passing `cache_read_input_tokens` and
        `cache_creation_input_tokens` only when `cache_aware`.
    *   `Gateway.price` gains keyword-only `cached_tokens: int = 0, cache_write_tokens: int = 0`,
        reads `self.settings.m7.token.cached_pricing`, and delegates; `_spend` passes
        `usage.cached_tokens` and `usage.cache_write_tokens`. Net line count of `gateway.py` is not
        greater than 499.
    *   `gateway/types.py` `Gateway` Protocol: the same keyword-defaulted signature (it declares
        `price` at line 180).
    *   `fake.py`: `FakeGateway.price` accepts the keywords and returns
        `(prompt + completion) * 1e-6 - cached * 0.9e-6 + written * 0.25e-6` (rounded to 9 places);
        `Scripted` gains `cached_tokens: int = 0` and `cache_write_tokens: int = 0`, which `stream`
        puts on the `Usage` it yields.
*   **Tests**: `tests/unit/test_gateway_pricing.py` (Create): with the real LiteLLM function and
    a Claude model from its bundled map, `price(cached=all)` < `price()` < `price(written=all)`;
    with `cached_pricing` off the keywords change nothing; keywords are not passed to
    `cost_per_token` when both are zero. `tests/unit/test_architecture.py` stays green (line cap).

#### Step 2. Tail layout: `src/auoreo/gateway/pipeline.py`, `src/auoreo/gateway/gateway.py` (Modify)

*   **Type / Scope**: request shaping, behind `m7.token.cache_layout`. Depends on nothing.
*   **Proposed Changes**: `ShapeContext` gains `cache_layout: str = "legacy"`; `_kwargs` sets it
    from `settings.m7.token.cache_layout` (one line). `_anthropic_cache` dispatches: `legacy` runs
    today's body unchanged; `tail` runs the same three marks and then `_mark_tail(msgs, cc, used)`
    implementing "Tail layout" above. A `role: "tool"` message gets `{**m, "cache_control": cc}`;
    other roles use `_mark_last_block`.
*   **Tests**: `tests/unit/test_gateway_pipeline.py` (Modify): `legacy` output identical to the
    current expected output; `tail` with tools gives four marks (tool, system, last user, last
    message) at the expected indices; a tool-loop request ending in `role: "tool"` gets the
    message-level key; a trailing assistant message with `content: None` and one whose last block
    is `thinking` are skipped in favour of the previous markable message; a last message equal to
    the last user message adds no fifth mark; no tools gives three marks; a non-Anthropic model is
    untouched. The Context7 citation for the provider limits goes in this step's commit body.

#### Step 3. Ledger and cache metrics: `src/auoreo/core/usage.py` (Modify), `src/auoreo/core/usage_metrics.py` (Create), `src/auoreo/core/metrics_text.py` (Modify)

*   **Type / Scope**: aggregation; additive, no flag. Depends on step 1 (the keywords).
*   **Proposed Changes**:
    *   `usage.py`: `PriceFn` becomes a `Protocol` with keyword-defaulted `cached_tokens` and
        `cache_write_tokens`; `UsageLedger.record` passes them only when non-zero. `totals`,
        `summary` and `series` select and return `cached_tokens` and `cache_write_tokens` (sums of
        the existing columns); `cache_hit_ratio(cached, prompt)` is defined here and applied to
        rows with `outcome == "ok"`. `UsageLedger.record` calls
        `usage_metrics.record_cache_usage(model, cached, cache_write)` next to `obs.record_usage`.
    *   `usage_metrics.py`: `record_cache_usage` emits `tokens_total{model, type="cached"}` and
        `{type="cache_write"}` through `observability.count` (label key `type` is already allowed).
    *   `metrics_text.HELP["tokens_total"]`: "by model and type (prompt|completion|cached|cache_write);
        cached and cache_write are subsets of prompt".
*   **Tests**: `tests/unit/test_usage.py` (Modify): the two three-argument price lambdas (lines
    22 and 43) take `**_`; a row with cached tokens still gets a non-None cost. New
    `tests/unit/test_usage_cache.py` (Create): aggregates sum the new columns; `series` buckets
    carry them; `cache_hit_ratio` is `0.0` for zero prompt and excludes `outcome=None` rows.
    `tests/unit/test_observability.py` (Modify): the new `type` values render with the updated HELP.

#### Step 4. Turn summary: `src/auoreo/runtime/recovery.py`, `src/auoreo/runtime/budget.py` (Modify)

*   **Type / Scope**: per-turn totals; additive. Depends on step 3 (`cache_hit_ratio`).
*   **Proposed Changes**: `StepOutput` gains `cached_tokens` and `cache_write_tokens`;
    `_Collect.feed` (case `Usage()`) adds them; `_merge` sums them across recoveries; `model_step`
    calls `budget.record(prompt, completion, calls, cached=, cache_write=)`.
    `TurnBudget.record` takes keyword-only `cached=0, cache_write=0`; `summary()` adds
    `cached_tokens`, `cache_write_tokens` and `cache_hit_ratio`.
*   **Tests**: `tests/unit/test_turn_budget_cache.py` (Create): `record` accumulates; `summary` keys
    and ratio; zero prompt gives `0.0`. `tests/unit/test_cache_turn.py` (Create): a two-step tool
    turn on `FakeGateway` with `Scripted(cached_tokens=..., cache_write_tokens=...)` yields a
    `turn.summary` whose `cache_hit_ratio` equals `sum(cached)/sum(prompt)`; a length-recovery
    merge keeps the cached counts.

#### Step 5. API and web: `src/auoreo/schema/chat.py`, `src/auoreo/schema/insights.py`, `web/app/src/pages/Usage.tsx`, `web/app/src/components/UsageCharts.tsx`, `web/e2e/pages.spec.ts` (Modify)

*   **Type / Scope**: wire contract and UI; additive. Depends on step 3.
*   **Proposed Changes**: `UsageTotals` (`chat.py`) and `UsagePoint` (`insights.py`) gain
    `cached_tokens: int = 0` and `cache_write_tokens: int = 0`; the routers need no edit because
    they call `model_validate` on the ledger dicts (`api/routers/usage.py:45,72`). The Usage page
    adds "Cached" and "Cache hit" columns (hit as a percentage, `0%` shown as `0%`); the chart adds a
    cached series. Regenerate: `uv run python scripts/check_openapi.py`, then
    `cd web && npm run gen:types` and `bash scripts/gen_sdk.sh`.
*   **Tests**: `uv run python scripts/check_openapi.py --check`; `tests/unit/test_usage_api.py`
    cases (Modify the existing usage API test if present, else add to `test_usage_cache.py`)
    assert the new JSON fields; the `pages.spec.ts` Usage case asserts the "Cached" header.
    Gate adds `cd web && npm run typecheck && npm run lint && npm run build`, then `npm run e2e`.

### Verification Plan

*   **Static analysis & linting**: `uv run ruff check .`, `uv run ruff format --check .`,
    `uv run mypy src` (no new errors).
*   **Unit tests**: `uv run pytest` (no new failure against the baseline list);
    `uv run pytest tests/unit/test_architecture.py` after steps 1 to 3 for the line caps;
    `uv run python scripts/check_openapi.py --check` after step 5.
*   **Web checks**: `cd web && npm run typecheck && npm run lint && npm run build` and
    `npm run e2e`.
*   **Manual**: `AUOREO_GATEWAY=fake uv run auoreo web --port 0`, then
    `uv run auoreo config set m7.token.cache_layout tail` and
    `uv run auoreo config set m7.token.cached_pricing true`; run a scripted tool turn. Pass: the
    Usage page shows the Cached and Cache hit columns; `curl /metrics` lists
    `tokens_total{type="cached"}`; `auoreo config set m7.token.cache_layout legacy` is applied
    live and the next request has three marks (checked by the unit tests, not by hand).
*   **Snapshots**: refresh with `AUOREO_SNAPSHOT=update` only if a snapshot legitimately changes
    (the `turn.summary` payload gains keys), never in CI.

--------------------------------------------------------------------------------

*Drafted with EGM*
