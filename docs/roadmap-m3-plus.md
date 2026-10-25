# AUOREO roadmap — M3 → M6

Status: M0–M2 shipped (`981b59b`): harness core, 20 folder tools, 3 agents, 2 workflows,
loop, gateway, memory, permissions, approvals, secrets, MCP bridge, API, CLI, 117 tests.
This document plans the rest of `spec.md` (written for "OREO"; names here are `auoreo`).

## 0. Rules that hold for every milestone

1. **Folders first.** A new capability is a new folder (or a new *folder kind*), never
   orchestration code. New kinds introduced below, all discovered by the existing
   `harness/discovery.py` → `loader.py` → `registry.py` path with one manifest model each:

   | Kind | Path | Manifest | Milestone |
   |---|---|---|---|
   | commands | `commands/<name>.md` | `CommandManifest` (frontmatter + body template) | M3 |
   | hooks | `hooks/<name>.yaml` | `HookManifest` (event, matcher, command/prompt, timeout) | M5 |
   | jobs | `jobs/<name>.yaml` | `JobManifest` (schedule, agent, prompt, delivery) | M5 |
   | webhooks | `webhooks/<route>.yaml` | `WebhookManifest` (provider, filters, template, agent) | M5 |
   | plugins | `plugins/<name>/plugin.yaml` | `PluginManifest` (provides, consumes, config schema) | M5 |
   | backends | `backends/<kind>/<name>/backend.yaml` + `impl.py` | `BackendManifest` (kind, requires, check) | M4 |

   Overlays (`~/.auoreo/<kind>`, `<cwd>/.auoreo/<kind>`) and `POST /api/harness/reload`
   work for every kind automatically.
2. **Hardware/cloud = backend with a fake.** Voice, vision, desktop control, tunnels,
   sandboxes and messaging are `backends/`/`plugins/` behind small ABCs in `src/auoreo/`.
   Each ships a deterministic fake used by tests here; real devices are a user checkpoint.
3. **Engine edits stay in seams:** `runtime/runner.py` gets hook points (pre/post tool,
   pre-step compaction check, stop), `core/services.py` gets new services, `api/app.py`
   gets routers. Nothing else in `src/` should need to know a feature exists.
4. **Every milestone:** ruff + mypy + pytest + `harness validate` + `check_openapi.py`
   green; live evidence against a real `auoreo serve` with the fake gateway saved to
   `.artifacts/`; commit + push; steering checkpoint.
5. Context7 before coding against a versioned dependency; library ID in the commit body.

Known M2 gaps carried into M3/M5: `smart` permission mode is a stub (→ M5g), no MCP
elicitation (→ M5g), compaction + sandbox deferred (→ M3a / M5i), Alembic deferred (→ M3d).

---

## M3 — Web UI, control plane, long sessions ✅ shipped (`9fa4138`)

Goal: a usable product for one person — browser UI on the same port, bidirectional control
over WebSocket, sessions that survive long conversations and can be forked/exported,
cost visibility, and schema migrations so upgrades never lose `state.db`.

### M3a Compaction (spec §11.9, cheapest-first subset S0, S2, S4, S5, S6, S7)
Files: `src/auoreo/runtime/compaction.py` (new), seams in `runner.py`, `prompts/compaction.md`
(the summarization instruction lives in a prompt section, overridable like any other).
- **S0 meter:** `effective = context_window − min(max_output, 20k)`; `auto = effective − 13k`;
  `blocking = effective − 3k`. Context window per model from `litellm.get_model_info`
  (gateway façade `context_window(model)`), fallback 128k, `[1m]` alias → 1M. Pressure =
  `gateway.count_tokens` over the assembled request (cached per history length).
- **S2 prune (no LLM):** tool results > 8000 chars from `bash/read/grep/glob/web_fetch`
  older than the last 10 messages → head 4096 + marker + tail 1024; commit only if ≥4096
  tokens reclaimed; never split a tool_call/result pair.
- **S4 triggers:** (a) pre-step pressure ≥ auto; (b) `GatewayError.code == "CONTEXT"` →
  compact with `retain=0`, retry the step once; (c) manual `POST /api/sessions/{id}/compact`
  and `/compact` command. Circuit breaker: 3 consecutive failures → stop + `run.failed{code:COMPACTION}`.
- **S5 summarize:** ONE call to the `small` routing group (aux), tool-free, temperature 0,
  template from `prompts/compaction.md`: Objective, Concepts, Files+Code (`path:why`),
  Errors+Fixes, Pending, Current work, Next step (verbatim quote), Critical context.
  Input capped at 160k chars; output `min(8192, max_output)`.
- **S6 replace:** protect head (system + first user message) and tail (last ≥20 messages
  and ≥1 real user message); replace the middle with one `user` checkpoint message
  `<compaction-summary>…</compaction-summary>`; move the replaced rows to a new
  `messages_archive` table (soft-archive, still searchable via `session_search`);
  emit `compaction.started/completed{preTokens, postTokens, trigger, archived}`.
- **S7 post-restore:** re-append the Tier-2 context snapshot; skills loaded this session
  are re-injected (names from `SessionState.loaded_skills`, cap 25k chars).
- Tests: meter math table; prune hysteresis; CONTEXT→compact→retry once; manual compact
  shrink proof (post < pre); archived rows searchable; byte-identical system prompt before/after.

### M3b WebSocket control plane (spec §5.2/§5.3)
Files: `src/auoreo/schema/ws.py` (contract, committed with this plan), `src/auoreo/api/ws.py`,
`src/auoreo/core/connections.py`.
- Single route `/ws?token=&last_seq=`; auth dependency runs **before** `accept()`
  (Bearer → `?token=` → cookie), failure closes 1008. Origin check for browsers.
- Replay `events WHERE seq > last_seq` (LIMIT 500, loop) then live tail from the bus with a
  per-connection `Queue(256)`; same `ServerEvent` envelope as SSE (never a second model).
- Inbound frames (text JSON, one object per frame; `\n`-joined batches tolerated on read):
  `hello`, `control_response` (permission reply / question answer by `request_id`),
  `control_request` (`interrupt` → coordinator.cancel, `steer`, `followup`), `pong`,
  `subscribe{channels}`. Unknown type → `error{subtype:unknown_type}`; binary → warn + drop.
- Outbound app `ping{tick, server_seq}` every 20s; close codes 1000/1001/1008/1011/1013/4003.
- Pending approvals + questions re-sent on (re)connect. Lifespan teardown closes all WS 1001.
- Tests: websockets client via uvicorn on an ephemeral port (ASGITransport has no WS):
  auth before accept, replay then live, approval round-trip over WS, interrupt, backpressure shed.

### M3c Sessions, titles, usage ledger
Files: `core/services.py` (fork/archive/delete/export), `api/routers/sessions.py`, new
`api/routers/usage.py`, `core/usage.py`, `runtime/titles.py`.
- `POST /api/sessions/{id}/fork` (copies committed messages, new `parent_id`, emits
  `session.forked{parent, at_idx}`), `POST …/archive|unarchive`, `DELETE …` (cascade
  messages, events kept), `GET …/export?format=json|md`.
- Title generation: after the first completed turn, one aux call (`small`, ≤12 words) in a
  background task; `session.title_updated` event; falls back to the first 60 chars.
- Usage ledger: `usage` table (session, run, model, prompt/completion tokens, cached,
  estimated, cost_usd) written from the `Usage` stream item; `GET /api/usage?session_id=`
  + `GET /api/usage/summary?days=`; `auoreo stats --days 7`; per-session `max_cost_usd`
  setting → `BUDGET` error that finishes the turn without new tool calls.
- Commands folder kind: `commands/compact.md`, `commands/model.md`, `commands/plan.md`,
  `commands/review.md`; `GET /api/commands`; `/name args` in a chat message expands via
  the body template (`{{args}}`), `compact`/`model` are engine-handled (`handler: compact|model`).

### M3d Alembic migrations
Files: `src/auoreo/core/alembic/` (`env.py` async template, `versions/0001_baseline.py`,
`0002_usage_archive.py`), `alembic.ini` at repo root, `core/db.py` runs `upgrade head` at
startup instead of `create_all` (keep `create_all` for `:memory:` tests), `SCHEMA_VERSION`
guard retained. `auoreo db upgrade|current|history`. Test: start on an M2 database file →
migrates in place → sessions preserved.

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
message, question form, token/cost badge, compact button), `/approvals` (cross-session
queue over WS), `/memory` (search, facts, USER.md/MEMORY.md viewer), `/harness`
(agents/tools/skills/workflows/mcp/prompts with source badges + reload), `/usage`
(SectionCards + per-session table), `/settings` (settings.json editor for non-secret keys,
secret *names* only), `/audit` (hash-chain status + recent rows), `/doctor` (mirror of CLI).
Cmd-K palette over commands + sessions + agents. Same-origin, no CORS in prod; token from
`<meta name="auoreo-token">` injected server-side when a password is set.
Evidence: Playwright (Node, pre-installed) e2e against `auoreo serve` + fake gateway: open
a session, send `/tool read …`, see tool card + answer, approve a `bash` call via the
footer, screenshots to `.artifacts/ui/`.

**M3 exit:** all of the above green; `auoreo web` opens a working UI; a 60-turn fake
conversation triggers compaction and continues; `state.db` from M2 upgrades in place.

---

## M4 — Perception and devices (voice, vision, browser, desktop, WebRTC)

Status: M4a/M4b/M4e shipped (`fe94817`); M4c voice + M4d desktop control shipped as fake-backed contracts (`2a22800`) — real STT/TTS/VAD/wake libraries and cua-driver need a hardware checkpoint; the media sidecar and outbound TTS track are open.

New folder kind `backends/<kind>/<name>/` with ABCs in `src/auoreo/backends/{stt,tts,vad,wake,capture,browser,desktop}.py`;
`settings.json: backends{stt: "faster_whisper", tts: "kittentts", …}` selects; `auoreo doctor`
reports each backend's `check()` (binary/model/device present) and license posture
(GPL/NC flagged, spec §13). Optional dependency groups: `auoreo[voice]`, `auoreo[vision]`,
`auoreo[rtc]` so the core install stays small.

- **M4a Multimodal + documents.** Gateway `LLMRequest` gains image parts (data-URL or
  file ref; ≤100 images, oldest stripped; 5 MB/8000 px re-encode ladder 85/70/50 JPEG);
  `vision_understand` tool (native when the model `supports_vision`, else aux `vision` group);
  tools `screenshot` (mss backend, fake = PNG fixture), `doc_read` (pypdf → pdfplumber tables
  → `pdftoppm -jpeg -r 100` pages → vision; `%PDF-` sniff; page cap), `sheet_read`
  (openpyxl), `ocr` (pytesseract backend when `tesseract` exists, else vision fallback —
  that is the path verifiable here), `image_info` (magic-byte sniff, SVG rejected).
- **M4b Browser tools (Playwright, verifiable here).** `browser_navigate/snapshot/click/
  type/screenshot/close` over playwright-python with `executablePath` override; accessibility
  snapshot with element refs (token-cheap), SSRF guard reused from `tools/net.py`, one
  browser per session, closed at turn end; `webfetch` permission + approval card showing URL.
  Spike result from spec §22-1 (Playwright for web) adopted; Lightpanda/CDP not in core.
- **M4c Voice.** Backends: STT `faster_whisper` (local, `base` int8; model download opt-in),
  `groq`, `openai`; TTS `kittentts` (local), `openai`, `elevenlabs`; VAD `silero`; wake
  `sherpa_onnx`. Media sidecar `auoreo-voice-host` (separate process, stdio JSON-lines
  `Hello/Ready/Capture/Transcript/Speak/Stop`), so audio libraries never load in the server.
  API: `POST /api/voice/transcribe` (multipart audio → text, 25 MB cap, ffmpeg normalize),
  `POST /api/voice/speak` (text → audio/ogg|mp3), `voice.*` events, barge-in →
  `control_request{steer}`. Push-to-talk widget in the UI. Verifiable here: WAV fixtures
  through fake backends + ffmpeg normalize; real STT/TTS need models/keys (checkpoint).
- **M4d Desktop control.** `computer` tool (`act{op, target, text, key} → {screenshot,
  verify}`) backed by `cua-driver` MCP stdio (`mcp/cua.yaml`, disabled by default; floor
  version ≥0.20), `delivery_mode=background` default, foreground separately approved,
  hard-blocks (lock/logout combos) in a floor. Not verifiable here (no display): fake
  backend + contract tests only; macOS/Windows checkpoint.
- **M4e WebRTC (aiortc sidecar).** `POST /api/rtc/offer` → answer; `auoreo-events` data
  channel mirrors the SSE envelope; audio track → STT backend; TTS → audio track;
  `response.cancel` → steer. Loopback test with two in-process peers (verifiable here).
  LiveKit stays an opt-in transport behind the same `VoiceTransport` ABC (spec §26.6), P2.

**M4 exit:** browser tools e2e green; document/vision tools pass fixture tests; voice and
desktop pass fake-backend tests and `doctor` prints an honest readiness matrix.

---

## M5 — Automation and multi-agent

Status: shared contracts (`fa3d896`), wave 1 M5a/b/c/d/e/g (`ab09e93`) and wave 2 shipped: M5h compat surfaces (`9069974`), M5f curator/skills-writer/locale (`0fab1dd`), M5i sandbox + ops (`7bec82f`). Open: docker/bwrap runs on a real host, `net: proxy` enforcement (no egress proxy yet), ACP `session/update` streaming.

- **M5a Webhook ingress (spec §5.1, §19.11).** Folder `webhooks/<route>.yaml`
  `{provider: github|generic, secret: "secret:webhook/<route>", events: [...], filters:
  [{field, equals|contains|regex}], agent, prompt: "{{event.action}} …", deliver: [log|github_comment|telegram]}`.
  Router `/hooks/{route}`: raw body first, `Content-Length` + 1 MiB cap → 413, HMAC
  (`X-Hub-Signature-256`, `compare_digest`) → 401, dedupe delivery id 1h, 30/min/route →
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
  command: "./scripts/check.sh", timeout_ms: 5000, on_error: warn|block}`. Command hooks
  get JSON on stdin and answer `{decision: allow|deny|modify, message?, input?}`; `Stop`
  hooks may `{block: true, reason}` (append + continue). Never run hooks on API-error messages.
- **M5d Teams (spec §16.1–16.6).** Tools `delegate_task` (spawn child session with an agent
  folder, `background`, concurrency 3, depth ≤2, `output_schema` validated, results as one
  `<task-notification>` message), `send_message(to|"*")`, `list_peers`, `wait_agent`,
  `interrupt_agent`; `teams/team_members/mailbox` tables; leaf agents lose `delegate_task/
  memory/send_message`; permission ceiling child ≤ parent; usage rolls up to the parent.
  Kanban-lite: `boards/tasks/comments` + `kanban_*` tools + atomic claim SQL. Agent folders
  `coordinator`, `reviewer`, `implementer`; workflow `research-implement-verify`.
- **M5e Plugins + connectors (spec §15).** Folder `plugins/<name>/plugin.yaml`
  `{name, version, provides: [tools|delivery|connector|backend], consumes, permissions,
  config_schema}` + `pyproject.toml`; installed to `~/.auoreo/plugins/<name>/.venv` with
  `uv venv` + `uv pip install --require-hashes`; enumerated via `entry_points(group="auoreo.plugin")`
  without importing; SHA256 pin in `lock.json`; subprocess bridge (JSON-RPC over stdio) so a
  plugin crash never takes the server down. First plugins: `telegram` (long-poll, pairing
  code, allowlist, Opus voice bubbles later), `discord`; both implement `DeliveryProvider`
  and `Connector` (inbound messages → sessions). Creator mode: `cordis_inspect/define/run/
  stop/undefine` tools register session-scoped dynamic tools via `Harness.register_dynamic`,
  approval-gated and audited; definitions vanish on restart.
- **M5f Ops, curator, skills-writer, research.** Tools `ops_cpu/mem/disk/net/procs`
  (psutil), `db_query` (allow-listed DSNs, SELECT-only, row cap, redaction), `monitor`
  job template; curator job (stale 30d/archive 90d, `.curator_ledger.jsonl`, never deletes);
  `skills-writer` agent + `skill_manage` tool (writes to `~/.auoreo/skills/`, lint
  frontmatter); researcher gets `web_search` (Exa → SearXNG chain, keys optional).
- **M5g Protocol depth.** MCP resources (`list_mcp_resources`, `read_mcp_resource`),
  elicitation form-mode → approval card, sampling via aux router (off by default);
  `smart` permission mode = guardian call on the `small` group (`APPROVE|DENY|ESCALATE`,
  timeout → escalate, 3 DENY breaker); `str_replace` alias tool; translation `{{locale}}`.
- **M5h Compat surfaces.** `POST /v1/chat/completions` + `GET /v1/models` (translate →
  gateway, permission/audit intact), `POST /mcp` streamable-HTTP **server** exposing the
  agent's tools to other clients (`MCPServer` + `Mcp-Session-Id`), `/acp/*` JSON-RPC
  (`session/new|prompt|cancel`), Python SDK via `openapi-python-client` into
  `src/auoreo_sdk/` (generated), TS client published from `web/sdk`.
- **M5i Sandbox (spec §19.8).** `backends/sandbox/{none,docker,bwrap}`; per-tool
  `net: none|proxy|direct` manifest column enforced in the executor; backend probe at
  startup → `warn|fail` policy, never silent downgrade; `write|edit` escalation
  `{requested_mode, justification}` one-call grant. Docker backend testable where a daemon
  exists; bwrap/Landlock via a helper binary later; fake backend in CI.

**M5 exit:** a GitHub webhook → agent → comment round trip (recorded fixtures), a cron job
that runs and delivers, a 3-agent delegated task, Telegram connector against a mocked
Bot API, hooks blocking a tool call, plugin installed from a local path and hot-reloaded.

---

## M6 — P2 backlog (plan only; each item gated on an M5 spike)

Object/face detection (RF-DETR + MediaPipe, Apache only; faces default-off), MQTT/miio
connectors (`paho-mqtt` v2), bandit-based model/effort policy (off by default, kill switch),
full swarm/blackboard, Starlark exec policy + guardian reviewer, remote plane (bridge/UDS/
multi-env/exec-server), LiveKit SFU/SIP telephony, E2B/Modal/Daytona environments,
GraphRAG/HyDE/late-chunking memory options, SQLCipher audit-at-rest, PWA/Electron shells,
PyInstaller `--onefile` matrix + npm launcher + GHCR image + signed auto-update channels.

---

## Verification matrix (this container vs. your machine)

| Area | Verifiable here | Needs you |
|---|---|---|
| Web UI, WS, compaction, migrations, usage | ✓ Playwright + fake gateway | real model quality |
| Browser tools | ✓ Chromium 1243 | — |
| Documents/vision | ✓ pypdf/pdfplumber/pdftoppm fixtures; vision fallback | tesseract OCR, real vision model |
| Voice | fake backends, ffmpeg normalize, sidecar protocol | mic/speaker, model downloads, keys |
| Desktop control | contract + fake | macOS/Windows + cua-driver |
| WebRTC | ✓ aiortc loopback | browser ↔ server over LAN/TURN |
| Tunnels | provider detection, fake | cloudflared/tailscale binaries |
| Webhooks/cron/teams/hooks | ✓ end-to-end with fakes | GitHub/Telegram tokens |
| Sandbox | fake backend; docker if daemon | bwrap/Landlock host, seatbelt |

## Execution model

Planning: Fable 5.1 (this document). Implementation: Opus 5.5 at medium effort, one agent
per workstream in its own git worktree with disjoint file ownership, merged by the
coordinator after gates pass; a steering checkpoint after each milestone. Milestone order
M3 → M4 → M5 → M6; inside a milestone, backend workstreams land before UI integration.
