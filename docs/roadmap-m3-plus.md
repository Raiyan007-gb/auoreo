# AUOREO roadmap — M3 → M6

Status: M0–M2 shipped (`981b59b`): harness core, 20 folder tools, 3 agents, 2 workflows,
loop, gateway, memory, permissions, approvals, secrets, MCP bridge, API, CLI, 117 tests.
This document plans the rest of `spec.md` (written for "OREO"; names here are `auoreo`).
Drift-checked against the code on 2026-11-04 (`.artifacts/egm/2026-11-03_DRIFT_REPORT.md`):
the statements below describe the code as it is, including renames made after each milestone.

## 0. Rules that hold for every milestone

1. **Folders first.** A new capability is a new folder (or a new *folder kind*), never
   orchestration code. New kinds introduced below, all discovered by the existing
   `harness/discovery.py` → `loader.py` → `registry.py` path with one manifest model each:

   | Kind | Path | Manifest | Milestone |
   |---|---|---|---|
   | commands | `commands/<name>.md` | `CommandManifest` (frontmatter + body template) | M3 |
   | hooks | `hooks/<name>.yaml` | `HookManifest` (event, matcher, `command` argv list, `timeout_ms`, `on_error`; no prompt variant — LLM-judged decisions are Starlark policies with `review` → guardian) | M5 |
   | jobs | `jobs/<name>.yaml` | `JobManifest` (schedule, agent, prompt, `deliver`) | M5 |
   | webhooks | `webhooks/<route>.yaml` | `WebhookManifest` (provider, filters, `prompt`, agent) | M5 |
   | plugins | `plugins/<name>/plugin.yaml` | `PluginManifest` (provides, consumes, config schema) | M5 |
   | backends | `backends/<kind>/<name>/backend.yaml` + `impl.py` | `BackendManifest` (kind, name, impl, default, requires_env, requires_binaries, license, posture); `check()` lives on the backend class | M4 |

   Overlays (`~/.auoreo/<kind>`, `<cwd>/.auoreo/<kind>`) and `POST /api/harness/reload`
   work for every kind automatically; the project overlay stays inert until `auoreo trust`.
2. **Hardware/cloud = backend with a fake.** Voice, vision, desktop control, tunnels,
   sandboxes and messaging are `backends/`/`plugins/` behind small ABCs in `src/auoreo/`.
   Each ships a deterministic fake used by tests here; real devices are a user checkpoint.
3. **Engine edits stay in seams:** the tool executor (`tools/executor.py`) runs the
   Pre/PostToolUse hooks, `runtime/stopgates.py` the Stop hooks, `runtime/runner.py` the
   pre-step compaction check; a new subsystem registers one line in
   `core/extensions.STARTERS` (never `services.py`); `api/app.py` gets routers. Nothing else
   in `src/` should need to know a feature exists.
4. **Every milestone:** ruff + mypy + pytest + `harness validate` + `check_openapi.py`
   green; live evidence against a real `auoreo serve` with the fake gateway saved to
   `.artifacts/`; commit + push; steering checkpoint.
5. Context7 before coding against a versioned dependency; library ID in the commit body.

Known M2 gaps carried into M3/M5, all closed since: `smart` permission mode (→ M5g,
`core/guardian.py`), MCP elicitation (→ M5g, `mcp/elicitation.py`), compaction + sandbox
(→ M3a / M5i, `core/sandbox_boot.py`), Alembic (→ M3d, `core/alembic/`).

---

## M3 — Web UI, control plane, long sessions ✅ shipped (`9fa4138`)

Goal: a usable product for one person — browser UI on the same port, bidirectional control
over WebSocket, sessions that survive long conversations and can be forked/exported,
cost visibility, and schema migrations so upgrades never lose `state.db`.

### M3a Compaction (spec §11.9, cheapest-first subset S0, S2, S4, S5, S6, S7)
Files: `src/auoreo/runtime/compaction.py` (new), seams in `runner.py`, `prompts/compaction.md`
(the summarization instruction lives in a prompt section, overridable like any other).
- **S0 meter** (`runtime/compaction_plan.py`): `reserve = min(max_output or 8192, 20k, window/4)`,
  `effective = window − reserve`, `auto = effective − min(13k, window/8)`, `blocking =
  effective − min(3k, window/16)`; `auto` is lowered further by `profile_threshold_ratio`,
  `threshold_tokens` and per-model overrides (`compaction_i4.meter`). Context window per
  model from the gateway probe (`gateway/ctxprobe.py`; `i2.probe_default` = 128k fallback,
  `[1m]` alias → 1M). Pressure = `gateway.count_tokens` over the assembled request, recounted
  on every check.
- **S2 prune (no LLM):** tool results > 8000 chars from the shell, read/grep/glob,
  web_fetch/web_search, codebase_map and edit/write/str_replace/apply_patch tools
  (`PRUNABLE_TOOLS`) older than the last 10 messages (the first 3 are protected too) → head
  4096 + marker + tail 1024; commit only if ≥4096 tokens reclaimed; never split a
  tool_call/result pair.
- **S4 triggers:** (a) pre-step pressure ≥ auto; (b) `GatewayError.code == "CONTEXT"` →
  compact with `retain=0`, retry the step once; (c) manual `POST /api/sessions/{id}/compact`
  and `/compact` command. Circuit breaker: 3 consecutive failures → stop + `run.failed{code:COMPACTION}`.
- **S5 summarize:** one summarize call per attempt through `resolve_aux_model("compaction")`
  (pin → `auxiliary.text_chain` → `small` → main), tool-free, temperature 0, template from
  `prompts/compaction.md`: Objective, Concepts, Files+Code (`path:why`), Errors+Fixes,
  Pending, Current work, Next step (verbatim quote), Critical context. Input capped at 160k
  chars; output fixed at 8192 tokens; timeout ladder 60→300→900 s and up to 3 CONTEXT
  retries (`compaction_summarize.py`).
- **S6 replace:** protect head (system + first user message) and tail (last ≥20 messages
  and ≥1 real user message); replace the middle with one `user` checkpoint message
  `<compaction-summary>…</compaction-summary>`; move the replaced rows to the
  `messages_archive` table (served by `GET /api/sessions/{id}/archive`; they stay searchable
  through the memory.db transcript index that `session_search` reads, written at commit
  time); emit `compaction.started{trigger, compaction_id}` and
  `compaction.completed{pre_tokens, post_tokens, trigger, archived}` (plus
  `compaction.warning|skipped|failed`).
- **S7 post-restore** (`runtime/compaction_restore.py`): a `<restored-context>` block inside
  the summary message — session metadata, ≤5 recently touched files, the skill bodies loaded
  in the compacted span (capped by `compaction.restore_skills_total_chars`), plan/todos —
  then the SessionStart hooks run again.
- Tests: meter math table; prune hysteresis; CONTEXT→compact→retry once; manual compact
  shrink proof (post < pre); archive endpoint; byte-identical system prompt before/after.

### M3b WebSocket control plane (spec §5.2/§5.3)
Files: `src/auoreo/schema/ws.py` (contract, committed with this plan), `src/auoreo/api/ws.py`,
`src/auoreo/core/connections.py`.
- Single route `/ws?token=&last_seq=`; auth dependency runs **before** `accept()`
  (Bearer → `?token=` → cookie), so a failure is refused at the handshake with HTTP 403
  (AGENTS.md, spec drift 7). Origin check for browsers.
- Replay `events WHERE seq > last_seq` (LIMIT 500, loop) then live tail from the bus with a
  per-connection `Queue(256)`; same `ServerEvent` envelope as SSE (never a second model).
- Inbound frames (text JSON, one object per frame; `\n`-joined batches tolerated on read):
  `hello`, `control_response` (permission reply / question answer by `request_id`),
  `control_request` (`interrupt` → coordinator.cancel, `steer`, `followup`, `edit`), `pong`,
  client `ping`, `subscribe{channels}` / `unsubscribe`. Unknown type →
  `control_response{subtype: error, code: unknown_type}`; malformed frames →
  `error{subtype: invalid_frame | binary_dropped | unknown_channel}` (binary → warn + drop).
- Outbound app `ping{tick, server_seq}` every 20s; close codes 1000/1001/1008/1011/1013,
  4001 (auth expired) and 4401 (revoked); 4003 is defined but never sent.
- Pending approvals + questions re-sent on (re)connect. Lifespan teardown closes all WS 1001.
- Tests: websockets client via uvicorn on an ephemeral port (ASGITransport has no WS):
  auth before accept, replay then live, approval round-trip over WS, interrupt, backpressure shed.

### M3c Sessions, titles, usage ledger
Files: `core/sessions_ops.py` (fork/archive/delete/export), `api/routers/sessions.py`,
`api/routers/usage.py`, `core/usage.py`, `runtime/titles.py`.
- `POST /api/sessions/{id}/fork` (copies committed messages, new `parent_id`, emits
  `session.forked{parent_id, at_idx}` on the child and `session.fork_created{child_id,
  at_idx}` on the parent), `POST …/archive|unarchive`, `DELETE …` (cascade messages, events
  kept), `GET …/export?format=json|md`.
- Title generation: after the first completed turn, one aux call (`resolve_aux_model("title")`,
  ≤8 words, 80-char cap) in a background task; `session.title_updated` event; falls back to
  the first 60 chars.
- Usage ledger: `usage` table (session, run, model, prompt/completion tokens, cached,
  estimated, cost_usd) written from the `Usage` stream item; `GET /api/usage?session_id=`
  + `GET /api/usage/summary?days=`; `auoreo stats --days 7`; the global `max_cost_usd`
  setting, evaluated over the session tree, ends the turn `blocked{code: BUDGET}` without
  new tool calls.
- Commands folder kind: `commands/compact.md`, `commands/model.md`, `commands/plan.md`,
  `commands/review.md` (plus `curator.md`, `goal.md`, `babysit_pr.md`); `GET /api/commands`;
  `/name args` in a chat message expands via the body template (`{{args}}`);
  `handler: compact|model|plan` are engine-handled, with `subtask/agent/worktree` fields.

### M3d Alembic migrations
Files: `src/auoreo/core/alembic/` (`env.py` async template, `versions/0001_baseline.py`,
`0002_schema_v2.py`, … up to `0008_settings_providers.py`; `SCHEMA_VERSION = 8`),
`alembic.ini` at repo root, `core/db_migrate.migrate()` called from `Database.init()` at
startup instead of `create_all` (kept for `:memory:` tests), `SCHEMA_VERSION` guard retained.
`auoreo db upgrade|current|history`. Test: start on an M2 database file → migrates in place →
sessions preserved.

### M3e Tunnels + CLI
Files: `core/tunnels.py`, `cli/commands/tunnel.py`, `cli/commands/web.py`, `cli/commands/db.py`.
- `auoreo tunnel start|stop|status --provider cloudflared|tailscale`: detect binary, spawn
  `cloudflared tunnel --url http://127.0.0.1:<port>` / `tailscale serve`, parse the public
  URL, print `wss://…/ws`, require `AUOREO_SERVER_PASSWORD`. Fake provider for tests.
- `auoreo web` = serve + open browser; `auoreo session export|fork|delete`;
  `auoreo run --format text|json|stream-json`; `auoreo logs --follow --session`.

### M3f Web UI (spec §17; Vite 8 + React 19 + TypeScript 6 + Tailwind 4 + shadcn)
Files: `web/app/` (Vite project), `web/sdk/types.gen.ts` (generated by
`openapi-typescript` from `web/sdk/openapi.json` — never hand-edited), `web/sdk/client.ts`
(thin fetch wrapper + `subscribeEvents` with `Last-Event-ID` resume + WS uplink hook),
`web/package.json` scripts: `dev` (proxy `/api`, `/ws` → 4096), `build` (→ `web/dist`,
served by FastAPI `app.mount("/")` after routers), `typecheck`, `lint`, `e2e`.
Pages (react-router, declarative): `/sessions` (table: new/continue/fork/archive/delete),
`/session/:id` (virtualized thread, streaming deltas, collapsible tool cards
pending→running→completed|error with spillover link, approval footer once/always/reject with
message, question form, token/cost badge, compact button), `/inbox` (cross-session approvals
and questions over WS; `/approvals` redirects), `/memory` (search, facts, USER.md/MEMORY.md
viewer), `/harness` (agents/tools/skills/workflows/mcp/prompts with source badges + reload),
`/usage` (SectionCards + per-session table), `/settings` (settings stored in `state.db`
`app_settings`, edited through the settings/secrets API; secret *names* only), `/audit`
(hash-chain status + recent rows), `/doctor` (mirror of CLI). Cmd-K palette over commands +
sessions + agents. Same-origin, no CORS in prod; browser auth is the HttpOnly cookie from
`POST /api/auth/login`, and a page served to a cookie-bearing request is marked
`<meta name="auoreo-auth" content="cookie">` (no token is ever embedded).
Evidence: Playwright (Node, pre-installed) e2e against `auoreo serve` + fake gateway: open
a session, send `/tool read …`, see tool card + answer, approve a `bash` call via the
footer, screenshots to `.artifacts/ui/`.

**M3 exit:** all of the above green; `auoreo web` opens a working UI; a 60-turn fake
conversation triggers compaction and continues; `state.db` from M2 upgrades in place.

---

## M4 — Perception and devices (voice, vision, browser, desktop, WebRTC)

Status: shipped — M4a/M4b/M4e (`fe94817`), M4c voice with real STT/TTS/VAD/wake backends plus
fakes (`2a22800`, then the `auoreo voice-host` sidecar and the outbound TTS track over WebRTC),
M4d desktop control (`cua_driver` backend, opt-in). Real devices, model downloads and keys
remain a hardware checkpoint; `tts/piper` and `wake/openwakeword` are stubs.

New folder kind `backends/<kind>/<name>/` with ABCs in `src/auoreo/backends/{stt,tts,vad,wake,capture,browser,desktop}.py`;
`settings.backends{stt: "faster_whisper", tts: "kittentts", …}` (stored in `state.db`;
`auoreo config set backends.stt=…`) selects, falling back to the manifest `default: true`;
`auoreo doctor` reports each backend's `check()` (binary/model/device present) and license
posture (GPL/NC flagged, spec §13). Extras: `auoreo[rtc]`, plus `voice-stream`,
`wake-porcupine` and `livekit`; the local STT/TTS/VAD/wake and perception libraries are
installed by hand or in the sidecar's venv (`docs/voice.md`, `docs/perception.md`), and the
document/vision libraries (pypdf, pdfplumber, openpyxl, pillow, mss, playwright) are core
dependencies.

- **M4a Multimodal + documents.** Gateway `LLMRequest` gains image parts (`ImagePart`:
  base64 + media type, built by the tools from a file; ≤100 images, oldest stripped; 5 MB/8000
  px re-encode ladder 85/70/50 JPEG); `vision_understand` tool (native when the model
  `supports_vision`, else aux `vision` group); tools `screenshot` (mss backend; the fake
  draws a PNG at runtime), `doc_read` (pypdf → pdfplumber tables
  → `pdftoppm -jpeg -r 100` pages → vision; `%PDF-` sniff; page cap), `sheet_read`
  (openpyxl), `ocr` (pytesseract backend when `tesseract` exists, else vision fallback —
  that is the path verifiable here), `image_info` (magic-byte sniff, SVG rejected).
- **M4b Browser tools (Playwright, verifiable here).** `browser_navigate/snapshot/click/
  type/screenshot/close` over playwright-python with the `browser_executable_path` override;
  accessibility snapshot with element refs (token-cheap), SSRF guard reused from
  `tools/net.py`, one browser context per session, closed on session delete, shutdown or
  `browser_close`; `webfetch` permission + approval card showing URL.
  Spike result from spec §22-1 (Playwright for web) adopted; Lightpanda/CDP not in core.
- **M4c Voice.** Backends: STT `faster_whisper` (local, `base` int8; model download opt-in),
  `groq`, `openai` (plus deepgram, deepinfra, elevenlabs, mistral, xai, local_command); TTS
  `kittentts` (local), `openai`, `elevenlabs`; VAD `silero`; wake `sherpa_onnx`. Media sidecar
  `auoreo voice-host` (separate process, JSON-RPC 2.0 lines: `check`, `transcribe`,
  `synthesize`, `vad`, `wake`, `shutdown`), selected with `backends.{stt,tts,vad} = sidecar`,
  so audio libraries stay out of the server. API: `POST /api/voice/transcribe` (multipart
  audio → text, 25 MB cap, ffmpeg normalize), `POST /api/voice/speak` (text → audio/ogg|mp3),
  `voice.*` events; barge-in stops playback, emits `voice.barge_in`, cancels the turn when
  `voice_barge_in_interrupts_turn` is set and submits the utterance as a steer. Push-to-talk
  widget in the UI. Verifiable here: synthesized WAVs (`tests/audio.py`) through fake backends
  + ffmpeg normalize; real STT/TTS need models/keys (checkpoint).
- **M4d Desktop control.** `computer` tool (`act{op, target, text, key} → {screenshot,
  verify}`) backed by the `cua_driver` desktop backend (spawns `cua-driver mcp` over stdio;
  version floor ≥0.20), opt-in via `settings.backends.desktop` (no desktop backend is selected
  by default), `delivery_mode=background` default, foreground separately approved, hard-blocks
  (lock/logout combos) in the tool's `pre_check` floor (`tools/computer/handler.py`). Not
  verifiable here (no display): fake backend + contract tests only; macOS/Windows checkpoint.
- **M4e WebRTC (aiortc sidecar).** `POST /api/rtc/offer` → answer; `auoreo-events` data
  channel mirrors the SSE envelope and accepts `subscribe` and `ping` only — control (steer,
  interrupt, approvals) stays WebSocket-only; audio track → STT backend; TTS → audio track;
  `response.cancel` exists on the LiveKit path. Loopback test with two in-process peers
  (verifiable here). LiveKit stays an opt-in transport behind the same `RtcTransport` ABC
  (spec §26.6), P2.

**M4 exit:** browser tools e2e green; document/vision tools pass fixture tests; voice and
desktop pass fake-backend tests and `doctor` prints an honest readiness matrix.

---

## M5 — Automation and multi-agent

Status: shared contracts (`fa3d896`), wave 1 M5a/b/c/d/e/g (`ab09e93`) and wave 2 shipped: M5h compat surfaces (`9069974`), M5f curator/skills_writer/locale (`0fab1dd`), M5i sandbox + ops (`7bec82f`). Done since the first cut: the egress proxy and `net: proxy` enforcement (`tools/egress.py`, `tools/netstage.py`; confinement of the proxy port needs Landlock ABI ≥ 4, seatbelt or docker, otherwise the proxy env is advisory), ACP `session/update` streaming (`api/routers/acp.py`). Still host-dependent: docker/bwrap runs on a real host.

- **M5a Webhook ingress (spec §5.1, §19.11).** Folder `webhooks/<route>.yaml`
  `{provider: github|generic, secret: "secret:webhook/<route>", events: [...], filters:
  [{field, equals|contains|regex}], agent, prompt: "{{event.action}} …", deliver: [log|github_comment|telegram]}`.
  Router `/hooks/{route}`: raw body first, `Content-Length` + 1 MiB cap → 413, HMAC
  (`X-Hub-Signature-256`, `compare_digest`) → 401, dedupe by delivery id for as long as the
  row is kept (`automation_deliveries_keep`, the newest 1000 finished rows), 30/min/route →
  429, then **202 after enqueue**; a `deliveries` table drives a background worker that runs
  the agent in its own session (`skip_memory`, constrained toolset, non-interactive) and
  posts the result via a delivery provider. `auoreo webhook list|test|deliveries|replay --dry-run`.
- **M5b Scheduler (spec §16.7).** Folder `jobs/<name>.yaml` `{schedule: "0 9 * * 1-5" |
  "every 30m" | ISO one-shot, tz, agent, prompt, skills, deliver, enabled}` + `~/.auoreo/jobs/`.
  APScheduler 3.x `AsyncIOScheduler` is trigger-only (`id=name, replace_existing=True,
  max_instances=1, coalesce=True, misfire_grace_time`); AUOREO owns the run (own session,
  `deliveries` claim-at-most-once `pending→delivering→delivered|failed`), catch-up
  `clamp(period/2, 120s, 2h)`. `GET/POST /api/cron`, `auoreo cron list|add --dry-run|run|pause`.
- **M5c Hooks engine.** Folder `hooks/<name>.yaml` `{event: PreToolUse|PostToolUse|
  SessionStart|SessionEnd|PreCompact|PostCompact|Stop|PermissionRequest, matcher: "bash|edit",
  command: ["./scripts/check.sh"], timeout_ms: 5000, on_error: warn|block}` (the command is an
  argv list, no shell). Command hooks
  get JSON on stdin and answer `{decision: allow|deny|modify, message?, input?}`; `Stop`
  hooks may `{block: true, reason}` (append + continue). Never run hooks on API-error messages.
- **M5d Teams (spec §16.1–16.6).** Tools `delegate_task` (spawn child session with an agent
  folder, `background`, concurrency 3, depth ≤2, `output_schema` validated, results as one
  `<task-notification>` message), `send_message(to|"*")`, `list_peers`, `wait_agent`,
  `interrupt_agent`; `teams/team_members/mailbox` tables; leaf agents lose `delegate_task/
  memory/send_message`; permission ceiling child ≤ parent; usage rolls up to the parent.
  Kanban-lite: `kanban_tasks` (board is a column) + `kanban_comments` + `kanban_*` tools +
  atomic claim SQL; the team blackboard (`teams/board.py`) stores in `artifacts`. Agent folders
  `coordinator`, `reviewer`, `implementer`; workflow `research-implement-verify`.
- **M5e Plugins + connectors (spec §15).** Folder `plugins/<name>/plugin.yaml`
  `{name, version, provides: [tools|delivery|connector|backend], consumes, permissions,
  config_schema}` + `pyproject.toml`; installed to `~/.auoreo/plugins/<name>/.venv` with
  `uv venv` + `uv pip install --require-hashes`; enumerated via `entry_points(group="auoreo.plugin")`
  without importing; SHA256 pin in `lock.json`; subprocess bridge (JSON-RPC over stdio) so a
  plugin crash never takes the server down. First plugins: `telegram` (long-poll, pairing
  code, allowlist, Opus voice notes), `discord`; both declare `provides: [connector,
  delivery]` and answer the `delivery.send` / `connector.start|stop` RPCs (inbound messages →
  sessions). Creator mode: `plugin_inspect/define/run/stop/undefine` tools register
  session-scoped dynamic tools via `Harness.register_dynamic`, approval-gated and audited;
  definitions vanish on restart.
- **M5f Ops, curator, skills_writer, research.** Tools `ops_cpu/mem/disk/net/procs`
  (psutil), `db_query` (allow-listed DSNs, SELECT-only, row cap, redaction), the `monitor`
  tool + `ops_monitor` job template; curator job (stale 30d/archive 90d,
  `.curator_ledger.jsonl`, never deletes); `skills_writer` agent + `skill_manage` tool (writes
  to `~/.auoreo/skills/`, lint frontmatter); researcher gets `web_search` (Exa → SearXNG
  chain, keys optional).
- **M5g Protocol depth.** MCP resources (`list_mcp_resources`, `read_mcp_resource`),
  elicitation form-mode → question card (Accept/Decline, then one question per field),
  sampling via aux router (off by default);
  `smart` permission mode = guardian call on the `small` group (`APPROVE|DENY|ESCALATE`,
  timeout → escalate, 3 DENY breaker); `str_replace` alias tool; translation `{{locale}}`.
- **M5h Compat surfaces.** `POST /v1/chat/completions` + `GET /v1/models` (translate →
  gateway, permission/audit intact), `POST /mcp` streamable-HTTP **server** exposing the
  agent's tools to other clients (low-level `mcp.server.Server` behind
  `StreamableHTTPSessionManager`, which owns `Mcp-Session-Id`), `POST /acp` JSON-RPC
  (`initialize`, `authenticate`, `session/new|prompt|cancel|load|set_mode|list|resume|close`),
  Python SDK via `openapi-python-client` into `sdk/python/` (`scripts/gen_sdk.sh`), TS client
  from `web/sdk`.
- **M5i Sandbox (spec §19.8).** `backends/sandbox/{none,docker,bwrap,seatbelt,fake}`;
  per-tool `net: none|proxy|direct` manifest column enforced in the executor; backend probe
  at startup → `warn|fail` policy, never silent downgrade; sandbox escalation (`sandbox_mode`
  + `justification`, one-call grant) for the shell tools (`bash`, `exec_command`) — `write`
  and `edit` are governed by path locks and the external-directory asks instead. Docker
  backend testable where a daemon exists; the Landlock helper (`backends/landlock.py`) is
  layered under bwrap and `none`; the `fake` backend stands in for an enforced sandbox in CI.

**M5 exit:** a GitHub webhook → agent → comment round trip
(`tests/integration/test_webhooks_roundtrip.py`: fake gateway + mocked GitHub API), a cron job
that runs and delivers, a 3-agent delegated task, Telegram connector against a mocked
Bot API, hooks blocking a tool call, plugin installed from a local path and hot-reloaded.

---

## M6 — P2 backlog ✅ shipped (`2508c57`)

Status: **wave 0** closed the M3–M5 gaps (`a8b5193`): Skills / Plugins & MCP / Jobs / Teams /
Audit / Doctor / Goals / Welcome pages, `/api/doctor` + `/api/audit[/verify]` + memory facts/files,
goals + consent-first suggestions (schema v4), rerank backends (none · fake · cross_encoder ·
cohere; fail-open) + `scripts/eval_memory.py`, opt-in OpenTelemetry, `lsp` (9 read-only ops),
tree-sitter `symbols`, persistent shell (`exec_command`/`write_stdin`), deferred tools kept
across reopened sessions. **Wave 1** (`6d9a849`, schema v5): Starlark exec policies
(`policies/*.star`, after floors / before rules; `review` → guardian; PermissionRequest hooks),
team blackboard + swarm/decide workflows + opt-in supervisor + analyst tools, effort wiring +
usage latency/outcome + opt-in epsilon-greedy model/effort/top_k bandit, opt-in HyDE /
GraphRAG-lite / contextual chunking, column encryption + SQLCipher + backups + multi-worker via
Redis (and the fresh-DB init race fixed). **Wave 2**: execution environments (local · docker ·
ssh · e2b · modal · daytona · remote) + `use_environment`, UDS daemon + exec-server + signed
instance bridge, perception (detection · faces default-off · sensors), MQTT/miio plugins,
`rtc_transport` (aiortc · LiveKit incl. SIP/egress helpers), ICE/TURN doctor rows. **Wave 3**:
multi-stage Dockerfile + compose, hatchling wheel bundling definitions + `web/dist`, PyInstaller
onefile (built and run on linux-x64), tag-driven `release.yml`, ed25519-signed `auoreo upgrade`
with channels + rollback, npm launcher, opt-in app-shell PWA, Electron shell.

Needs your hardware/accounts to verify: real model weights (RF-DETR, Grounding DINO, YOLO-ONNX,
MediaPipe, InsightFace, cross-encoder beyond MiniLM), cameras, MQTT brokers/miio devices, a
LiveKit server/SIP trunk, E2B/Modal/Daytona, a Docker daemon, macOS/Windows binaries and signing
certificates, and the `UPDATE_SIGNING_KEY` release secret.


Object/face detection (RF-DETR, Grounding DINO and MediaPipe under Apache; YOLO-ONNX is AGPL and
runs in a sidecar only; InsightFace weights are non-commercial behind `face_nc_opt_in`; faces
default-off), MQTT/miio connectors (`paho-mqtt` v2), bandit-based model/effort/top_k policy
(off by default, kill switch), full swarm/blackboard, Starlark exec policy + guardian reviewer,
remote plane (bridge/UDS/multi-env/exec-server), LiveKit SFU/SIP telephony, E2B/Modal/Daytona
environments, GraphRAG/HyDE/contextual-chunking memory options, SQLCipher audit-at-rest,
PWA/Electron shells, PyInstaller `--onefile` matrix + npm launcher + GHCR image + signed
auto-update channels.

**M6 spikes (F7, decision records in `docs/spikes/`):**
- Image generation: `plugins/imagegen` (openai `gpt-image-1` · fal FLUX · fake), off by default,
  approval-gated, cost in every result; local SD not bundled. The host gap found by the spike
  (no plugin → usage/audit call) is closed: `usage.record` and `audit.record` plugin requests.
- RL-vision: no perception policy shipped. Offline, retuning the static default gains +0.26 reward
  and a contextual bandit adds only +0.04, with a reward (F1) the tool cannot observe online.
- HTTP/3: stay on uvicorn (HTTP/1.1). Hypercorn h3 works, incl. SSE + WS; ship `deploy/caddy/Caddyfile`
  (h1/h2/h3 edge, SSE verified over h3 end to end; Caddy refuses WS over h3, browsers use h1/h2).
- Nuitka: PyInstaller stays (150 s build, 203.9 MB). The Nuitka onefile hit litellm optimizer crashes
  (worked around), then needed 7,175 modules compiled to C and was stopped at the 25 min time box.
  `scripts/build_binary_nuitka.py` is kept as experimental.
- Tauri: Electron stays. The Tauri scaffold builds (4.6 MB vs Electron's 274 MB runtime), but the ~200 MB
  server dominates, signing is unchanged, and window auth is missing.
- ACP: v1 `session/list` · `session/resume` · `session/close` + `sessionCapabilities`; the client
  methods (permission/fs/terminal) need a bidirectional transport.

## Spec closure ✅ (G + H waves)

Two read-only audits (§2 catalog + §21 milestones; §5–§26 normative) drove two more waves.

- **G (catalog/normative gaps):**
  - gateway retries and fallbacks;
  - agent loop liveness and busy windows;
  - webhook V2 signing, rotation and providers;
  - Host/CSRF/cookie auth, WS extensions and a bounded inbox;
  - log redaction with sentinel mode and a write-back guard;
  - secret scope and audit, external-directory checks and sensitive-write asks;
  - an unattended-approval policy;
  - Landlock and seatbelt sandboxing;
  - compaction restore, PTL retry, idle and partial compaction, and memory nudges;
  - prompt `toolOrder` and families;
  - terminal_pty, jobs, monitor and ops_gpu/ops_ping;
  - DNS-pinned fetch and apply_patch V4A;
  - tool-engine gating (`check_fn`/`env_gate`), lossless args, `tool.progress`, path locks and MCP sampling;
  - structlog JSON, `/metrics`, cassettes, coverage floor and gitleaks;
  - the full CLI/session surface;
  - OpenAI Realtime, streaming STT, PREFS proposals, the skills hub and review subtasks.
- **H (audit fixes):**
  - argv-based hardline and ask floors, plus a shell secret-file scanner;
  - `.env` can no longer set security knobs (nothing loads a `.env` at startup at all now);
  - forced debug redaction and the secret precedence env → OS keyring → secrets.db (no `.env`
    tier: the only `.env` reader is the one-time, user-confirmed web import);
  - auto sandbox (bwrap on Linux) and strict webhook secrets;
  - the leaf role, the root cost cap, the delegate_task actions/fork/budget/pause/heartbeat, and resume_agent/stop_task;
  - the cronjob tool and chat `/approve`;
  - webhook filters, script and CIDR support, and transform hooks;
  - plugin and MCP skills in the catalog, plus `@skill` mentions;
  - plan review, a thinking stream, approval cascade, usage charts and per-session sources;
  - the remaining CLI commands;
  - the Python grep fallback, rust/go LSP and the apply_patch format step;
  - the `forecast`, `run_code`, `review`, `video_analyze` and `workflow` tools;
  - email and calendar plugins;
  - the computer-use lock;
  - the spec teardown order, SSE `events:tail` and the WS `control_response` error;
  - JWT cookies, age-encrypted secrets export, plugin quarantine, literal-secret asks and PTY shells inside the sandbox.

- **I (third audit, §1–§26 in three passes):**
  - security: capability-scoped `/ws`, creator grants keyed by package hash, subagent deny
    inheritance, cookie-JWT WS expiry, plan-dir containment, LiveKit token hygiene, debug-bundle
    privacy, MCP elicitation URL mode, owner-only key files, denial/guardian breakers,
    split-secret redaction, a project `.env` import allowlist, `/compact` idle lock, workspace trust;
  - gateway: rotation grace + secrets API hot reload, key pools, daily budgets, structured output,
    tool-schema shaper, partial tool-call events, OpenRouter lane, `best`/`[1m]`, ledger v6/v7;
  - teams: kanban dispatcher + guards, cron fields/schedules/aliases/notify, exact fork, stale
    idle-vs-in-tool, coordinator allowlist + `synthetic_output`, handoff schema, `sa-` ids;
  - memory: re-embed job, topics/`memory_read`/drift guard/taxonomy, scopes + tenants + decay,
    CJK FTS, docs chunking, a 230-row golden set gated in CI, ralph-lite loops;
  - plugins/MCP: trust tiers + signatures + pinned pip installs, `list_changed`/roots/SSE,
    sampling caps, bundled MCP servers, `WebhookProvider` + GitHub `/meta` CIDRs;
  - messaging: Discord gateway connector, Telegram streaming/voice, `MEDIA:` voice notes, email
    STARTTLS/XOAUTH2, Google Calendar v3, GitHub/Google OAuth connectors;
  - web/CLI: share page, skills hub UI, diff view, edit-and-approve, `--json`/`--mini`, `queue`,
    `pairing`, `reload`, `doctor --fix`, new doctor rows;
  - voice/CU: wake daemon, streaming STT in RTC, STT filters, LiveKit agents worker + token/webhook,
    desktop safety guards, Chrome CDP bridge, vision remap/retry;
  - ops: per-tool egress proxy + `net` stage, search backends (Brave/Tavily/Jina/Firecrawl), npm +
    Electron release jobs, `install.sh`, SHA-pinned CI + Dependabot + e2e, lane cassettes,
    session snapshots, tool-catalog check, PR template, spike records.

Deliberately left as documented limits are listed in each merge commit body (e.g. Discord has
no streaming or voice channels, `livekit-agents` is not a declared extra because it cannot
resolve alongside the `otel` extra, the cua-driver command names are contract-tested only, and
Sigstore provenance for plugins is not built).

---

## Verification matrix (this container vs. your machine)

| Area | Verifiable here | Needs you |
|---|---|---|
| Web UI, WS, compaction, migrations, usage | ✓ Playwright + fake gateway | real model quality |
| Browser tools | ✓ Chromium 1243 | — |
| Documents/vision | ✓ pypdf/pdfplumber/pdftoppm fixtures; vision fallback | tesseract OCR, real vision model |
| Voice | fake backends, ffmpeg normalize, sidecar protocol | mic/speaker, model downloads, keys |
| Desktop control | contract + fake (opt-in backend) | macOS/Windows + cua-driver |
| WebRTC | ✓ aiortc loopback | browser ↔ server over LAN/TURN |
| Tunnels | provider detection, fake | cloudflared/tailscale binaries |
| Webhooks/cron/teams/hooks | ✓ end-to-end with fakes (incl. webhook → agent → GitHub comment) | GitHub/Telegram tokens |
| Sandbox | ✓ bwrap + Landlock (enforced here); docker if daemon; `fake` for enforced-path tests | seatbelt (macOS) |
| Perception (detection, faces, sensors) | fake backends + contract tests | model weights, cameras |
| MQTT/miio, LiveKit/SIP, E2B/Modal/Daytona | fakes and mocked APIs | brokers, devices, servers, accounts |
| Binaries + signing | linux-x64 PyInstaller build | macOS/Windows builds, certificates, `UPDATE_SIGNING_KEY` |

## Execution model

Planning: Fable 5.1 (this document). Implementation: Opus 5.5 at medium effort, one agent
per workstream in its own git worktree with disjoint file ownership, merged by the
coordinator after gates pass; a steering checkpoint after each milestone. Milestone order
M3 → M4 → M5 → M6; inside a milestone, backend workstreams land before UI integration.
