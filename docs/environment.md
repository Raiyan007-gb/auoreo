# Environment: models, settings, secrets and custom variables from the web

There is **no `.env` file** and no `.env.example`. AUOREO keeps its configuration in two stores in
the runtime home (`~/.auoreo`, Windows `%LOCALAPPDATA%\auoreo`) and you manage both from the web:

| What | Stored in | Managed from |
|---|---|---|
| settings (`AUOREO_MODEL`, `AUOREO_LOCALE`, `models`, …) | `state.db`, table `app_settings` (one row per top-level key, JSON value; last 10 change sets in `app_settings_history`) | Settings → Models / Environment, `auoreo config` |
| keys and other secrets (`OPENAI_API_KEY`, `EXA_API_KEY`, …) | `secrets.db` (Fernet-encrypted, key in the OS keyring or a 0600 file) | Settings → Models / Environment, `auoreo secrets` |
| provider connections + cached model lists | `state.db`, `provider_connections` / `provider_models` (the key's **name** only) | Settings → Models |
| custom variables | plain: `app_settings` `env.custom`; secret: value in secrets.db, flags in `app_settings` | Settings → Environment |

## Settings → Models

1. **Add provider** — paste an API key. The provider is detected from the key's shape
   (`sk-or-` OpenRouter, `sk-ant-` Anthropic, `sk-proj-`/`sk-svcacct-` OpenAI, `gsk_` Groq, `xai-` xAI,
   `AIza` Gemini, `fw_` Fireworks, `tgp_` Together); an ambiguous key (a bare `sk-…` could be OpenAI or
   DeepSeek, prefix-less keys Mistral / Cohere / Together / Azure) asks you to choose. Ollama needs a
   base URL (no key), Azure OpenAI an endpoint, an API version and the **deployment names** (Azure has
   no data-plane "list deployments", so they are typed in).
2. **Connect** validates the key by listing the provider's models. Only on success is the key stored
   (the provider's LiteLLM env name for its first key, e.g. `OPENAI_API_KEY`; further keys of the same
   provider go to `provider/<id>/<slot>` and form a key pool), the connection row created and the model
   list cached. A failure shows the provider's own error (redacted) and stores nothing.
3. **Choose models** — pick a connected provider, search its models (id, name, context window,
   prices when the provider publishes them, kind) and press *Use as main*, *Use as small* or *Use for
   embeddings*. This writes `model` / `small_model` / `embed_model` (+ `embed_dims` when the width is
   known) as `<litellm prefix><model id>` through the live-settings path: the gateway switches from the
   agent's **next step**, no restart (the embedding model is read when the memory index opens, so it
   applies after a restart).

The model list is cached for 1 hour and refreshed on demand; a failed refresh keeps the old list and
marks the connection (`invalid` = key rejected, `error` = anything else). Removing a connection that
serves the selected main / small / embedding model asks first (`DELETE …?force=1`).

| Provider | List endpoint | Notes |
|---|---|---|
| OpenRouter | `GET /api/v1/models` | key checked with `GET /api/v1/key` first (the catalog is public); prices + context |
| Anthropic | `GET /v1/models` (`x-api-key`, `anthropic-version`) | paginated |
| OpenAI, Groq, xAI, Mistral, DeepSeek, Together, Fireworks | OpenAI-compatible `GET …/models` | embeddings / TTS / Whisper / image models are kept but marked `embedding` / `other` |
| Gemini | `GET v1beta/models?key=` | `supportedGenerationMethods` decides chat vs embedding |
| Cohere | `GET /v1/models` | `endpoints` decides chat vs embedding |
| Ollama | `GET {base}/api/tags` | one connection; the base URL is stored as `OLLAMA_API_BASE` |
| Azure OpenAI | `GET {endpoint}/openai/models?api-version=` | one connection; deployments entered by hand; `AZURE_API_BASE` / `AZURE_API_VERSION` |

## The real process environment still wins

For containers and CI a variable set in the **real process environment** (`docker run -e`,
`export`) overrides the database; such a row is read-only in the web (409, "set in the real
environment"). Two groups are only ever read from the environment (or a CLI flag):

- **bootstrap** — `AUOREO_HOME`, `AUOREO_PROFILE`, `AUOREO_DB_ENCRYPTION`, `AUOREO_HOST`, `AUOREO_PORT`,
  `AUOREO_WORKERS`: they are needed before `state.db` can be opened (or are bound before it is), so the
  database never stores them (403 in the web; `auoreo config set port …` is refused).
- **`[env-only]` security knobs** — `AUOREO_PERMISSION_MODE`, `AUOREO_YOLO`, `AUOREO_SANDBOX_*`,
  `AUOREO_BACKENDS`, `AUOREO_REDACT_*`, `AUOREO_SECURITY`, `AUOREO_APPROVALS`, `AUOREO_KEYRING`,
  `AUOREO_HTTP`, `AUOREO_WEBHOOKS`, … (`core/env_rules.py`): frozen from the real environment at startup.

Precedence for a setting: real environment > startup flag (`auoreo serve --port …`) > `state.db` >
built-in default. For a key: real environment > OS keyring > `secrets.db`.

## Moving off an old `.env` / `settings.json`

- **`settings.json`** (before this version) is migrated once, automatically: the first start (or
  any `Settings()` built by the CLI) imports it into `app_settings` when that table is empty, drops
  bootstrap / invalid keys (logged), renames the file `settings.json.migrated-<timestamp>` and the
  server records an audit row `settings.migrate`. Nothing is read from a `settings.json` afterwards.
- **`.env` files are never loaded.** Settings → Environment → **Import existing .env** is the one
  migration path: it previews, then (after you confirm) reads `~/.auoreo/.env` and the current
  project's `.env`, moves keys into secrets.db and `AUOREO_*` settings into `state.db`, and turns
  provider keys into validated provider connections. Security knobs and bootstrap fields are never
  imported; a project `.env` (repository-controlled) may only contribute provider keys and
  model/agent names. Then delete the files — `auoreo doctor` warns while one exists.

## Live vs restart

A write validates the whole stored settings (an invalid value answers 422 and changes nothing),
saves them in one transaction, and `core.live_settings.apply` copies the changed fields **into the
running settings object**. Caches are refreshed per area: the LiteLLM Router and credentials, harness
reload, plugins, scheduler, hooks, voice, delivery workers, Sentry / trace file, cached backends and
custom-variable redaction. A new secret reaches the gateway at once.

A watcher polls the store's version counter (`app_settings_meta.version`, bumped by every write
from any process) and the secrets.db mtime every 2 s, so `auoreo config set` and `auoreo secrets set`
in another terminal apply live too. Events carry names only: `settings.changed {keys, live,
restart_required, source}`, `secret.changed {name, action}`, `providers.changed {action,
connection_id, provider, status}`, `models.changed {roles, values}`.

**Restart required** (only read at startup; the row says why): `redis_url`, `server_password`,
`gateway`, `cors_origins`, `mcp_server_enabled`, `cli`, `cli2`, `logging`, `otel_mode`, `memory` /
`embed_model` / `embed_dims` / `i4`, `plugins_python`, `voice_host_python`, `voice_host_backends`,
`i8`, `livekit`, `i3`, `automation_workers`, `automation_start_delay_s`, `browser_executable_path`,
plus the bootstrap fields and every `[env-only]` knob. **Undo last change** restores the previous
stored change set (ten are kept).

## CLI

`auoreo config path` (state.db), `get [key]` (effective values), `list` (stored values only),
`set <dotted.key> <json|string>`, `unset <key>`, `undo`, `edit` (JSON in `$EDITOR`, validated, then
written back). `auoreo secrets set|rotate|delete` manage secrets.db.

## Custom variables

Names match `^[A-Z][A-Z0-9_]{1,63}$` and may not be `AUOREO_*`, a catalog name, or a loader /
redirect name (`PATH`, `HOME`, `SHELL`, `TMPDIR`, `LD_*`, `DYLD_*`, `PYTHON*`, `NODE_*`, `GIT_*`,
`*_PROXY`, `*_URL`, `*_HOST`, `SSL_CERT*`, `LITELLM_*`).

- **Secret** — the value goes to secrets.db and is never shown again.
- **Available to shell commands** — exported to `bash`, `exec_command`, `terminal_pty`, `monitor`,
  `run_code`, `pwsh` and `auoreo exec`, on top of the scrubbed child environment; secret values are
  audited as `secret.read{name, by}` and masked (`***`) in tool output and events.
- **Show to the agent** — listed in the per-turn context block (never the system prompt).
- `{{var.NAME}}` resolves a plain variable in slash-command templates, job prompts and workflow
  step inputs; a secret or unknown name renders empty.

## API

All under normal auth with the cookie CSRF check; per-session capability tokens are refused;
bodies are capped at 64 KiB; no response, event or audit row ever contains a key or secret value.

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/providers/detect` | `{key}` → candidate providers (nothing stored) |
| GET / POST | `/api/providers` | connections (no keys) + provider specs / validate + connect |
| PUT / DELETE | `/api/providers/{id}` | replace key / label / endpoint (validated) / remove (`?force=1`) |
| POST | `/api/providers/{id}/refresh` | refetch the model list |
| GET | `/api/providers/{id}/models` | `?kind=&q=&refresh=1` cached model list |
| GET / PUT | `/api/models/selection` | main / small / embedding (`{connection_id, model_id}` each) |
| GET | `/api/env` | catalog by section: value / `is_set`, source (`env`, `db`, `secrets`, `override`, `default`), tier, … |
| PUT | `/api/env/settings/{NAME}` | `{value}` (null = default) → state.db, applied live |
| PUT / DELETE | `/api/env/secrets/{NAME}` | write-only secret |
| POST | `/api/env/secrets/{NAME}/rotate` | new value, the old one valid for `grace_s` |
| POST / PUT / DELETE | `/api/env/custom[/{NAME}]` | custom variables |
| POST | `/api/env/undo` | restore the previous stored settings |
| POST | `/api/env/import-dotenv` | `{dry_run}` the one-time `.env` import |

Audit rows: `settings.write{keys}`, `settings.undo`, `settings.migrate`, `secret.write{name}`,
`env.import{counts}`, `provider.add|update|delete|refresh`, `models.select{values}`.

Tests only: with the fake gateway, `AUOREO_TEST_PROVIDER_BASE` (real environment) points provider
model-list calls at a local server (the Playwright harness serves an OpenAI-compatible `/v1/models`).
