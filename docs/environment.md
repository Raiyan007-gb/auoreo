# Environment: settings, secrets and custom variables from the web

**Settings → Environment** in the web UI manages every variable listed in `.env.example`, plus
your own custom variables, and applies changes to the running server without a restart
wherever that is possible. The same data backs `.env.example` (`scripts/gen_env_example.py`)
and `GET /api/env`: one catalog in `src/auoreo/core/env_catalog_data.py`.

## Where a value lives

| Kind | Examples | Stored in | Shown in the web |
|---|---|---|---|
| setting | `AUOREO_MODEL`, `AUOREO_LOCALE`, `AUOREO_MODELS` | `~/.auoreo/settings.json` (validated, 0600, last 10 versions in `settings.history/`) | the effective value (redacted) |
| secret | `OPENAI_API_KEY`, `EXA_API_KEY`, `AZURE_API_BASE` | `~/.auoreo/secrets.db` (Fernet-encrypted) | `is_set` + "updated …" only — **write-only** |
| pass-through | `GH_HOST`, `HTTPS_PROXY`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `AUOREO_URL` | the process environment | read-only |
| custom | `MY_VARIABLE` | plain: settings.json `env.custom`; secret: value in secrets.db, flags in settings.json | plain value / `is_set` |

The web never writes a `.env` file. **Import from ~/.auoreo/.env** moves the keys of your home
`.env` into secrets.db and its `AUOREO_*` settings into settings.json (security knobs are never
imported), then recommends clearing the file. A project `.env` is never read by the import.

## Precedence and sources

Each row has a `source` badge:

1. `environment` — the real process environment. **It always wins**, so such a row is read-only
   in the web (a write answers 409 and says to unset it there).
2. `startup flag` — a value the server was started with (`auoreo serve --port …`); it keeps
   winning over settings.json until the next start (409).
3. `.env file` — put into the environment by a `.env` file at startup. Settings from a `.env` are
   read-only until imported (409); secrets in secrets.db already win over `.env` values.
4. `settings.json` / `secrets.db` — managed here.
5. `default` — the built-in default.

`[env-only]` security knobs (`AUOREO_PERMISSION_MODE`, `AUOREO_YOLO`, `AUOREO_SANDBOX_*`,
`AUOREO_BACKENDS`, `AUOREO_REDACT_*`, `AUOREO_SECURITY`, `AUOREO_APPROVALS`, `AUOREO_KEYRING`,
`AUOREO_HOME`, `AUOREO_PROFILE`, `AUOREO_HTTP`, `AUOREO_WEBHOOKS`, …) are shown with their
effective value but are read-only (403): they are frozen from the real environment at startup.
Editing them from the web would be a separate, opt-in step-up (`core.env_view.EnvPolicy`).

## Live vs restart

A write validates the whole settings.json (an invalid value answers 422 and leaves the file
untouched), saves it, and `core.live_settings.apply` copies the changed fields **into the running
settings object** — every service holding it sees the new value on its next read. The agent
uses it from its **next step** (a step already in flight keeps its request). Caches are refreshed
per area: the LiteLLM Router and credentials (models, aliases, fallbacks, auxiliary routing,
tuning, i2), a harness reload (`strict_overlays`, `tool_engine`), plugins (enable/config), the
scheduler, hooks, voice, delivery workers, Sentry / trace file, cached rerank / detection / STT /
TTS backends, and custom-variable redaction. A new secret reaches the gateway at once.

A watcher polls settings.json and secrets.db (every 2 s), so `auoreo config set`,
`auoreo secrets set` and hand edits apply live too. Events carry names only:
`settings.changed {keys, live, restart_required, source}` and `secret.changed {name, action}`.

**Restart required** (only read at startup; the row says why): `home`, `host`, `port`, `workers`,
`redis_url`, `db_encryption`, `server_password`, `gateway`, `cors_origins`,
`mcp_server_enabled`, `cli`, `cli2`, `logging`, `otel_mode`, `memory` / `embed_model` /
`embed_dims` / `i4` (the memory index), `plugins_python`, `voice_host_python`,
`voice_host_backends`, `i8`, `livekit`, `i3`, `automation_workers`,
`automation_start_delay_s`, `browser_executable_path`, and every `[env-only]` knob. A saved but
not yet active value shows "pending restart".

**Undo last change** restores the previous settings.json version (ten are kept).

## Custom variables

Names match `^[A-Z][A-Z0-9_]{1,63}$` and may not be `AUOREO_*`, a catalog name, or a loader /
redirect name (`PATH`, `HOME`, `SHELL`, `TMPDIR`, `LD_*`, `DYLD_*`, `PYTHON*`, `NODE_*`, `GIT_*`,
`*_PROXY`, `*_URL`, `*_HOST`, `SSL_CERT*`, `LITELLM_*`).

- **Secret** — the value goes to secrets.db and is never shown again.
- **Available to shell commands** — exported to `bash`, `exec_command`, `terminal_pty`,
  `monitor`, `run_code`, `pwsh` and `auoreo exec`, on top of the scrubbed child environment. A
  secret value is audited as `secret.read{name, by}` when injected and masked (`***`) in tool
  output and events. Other tools never see it.
- **Show to the agent** — the name (and a plain value) is listed in the per-turn context block —
  never the system prompt, so the prompt cache stays stable. Secret values are never listed.
- `{{var.NAME}}` resolves a plain variable in slash-command templates, job prompts and workflow
  step inputs at render time; a secret or unknown name renders empty.

## API

All under normal auth with the cookie CSRF check; per-session capability tokens are refused;
bodies are capped at 64 KiB; no response ever contains a secret value.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/env` | catalog by section: value / `is_set`, source, tier, type, choices, default, live, writable |
| PUT | `/api/env/settings/{NAME}` | `{value}` (null = default) → settings.json, applied live |
| PUT / DELETE | `/api/env/secrets/{NAME}` | write-only secret |
| POST | `/api/env/secrets/{NAME}/rotate` | new value, the old one valid for `grace_s` |
| POST / PUT / DELETE | `/api/env/custom[/{NAME}]` | custom variables |
| POST | `/api/env/undo` | restore the previous settings.json |
| POST | `/api/env/import-dotenv` | `{dry_run}` move `~/.auoreo/.env` into the stores |

Audit rows: `settings.write{keys}`, `secret.write{name}`, `env.import{counts}`, `settings.undo`.
