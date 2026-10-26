# plugins/

`plugins/<name>/plugin.yaml` + `pyproject.toml` + `plugin.py`. Plugins run **out of
process** in their own virtualenv (`~/.auoreo/plugins/<name>/.venv`) and talk JSON-RPC over
stdio, so a crashing plugin cannot take the server down. What a plugin provides (tools,
delivery providers, connectors, backends, skills) is registered dynamically on start.

```yaml
name: telegram
version: 0.1.0
description: Telegram bot connector + delivery provider.
provides: [connector, delivery]
consumes: [sessions, "secrets:telegram/bot_token"]
permissions: [messaging]
config_schema:
  type: object
  properties: {allowlist: {type: array, items: {type: string}}}
  additionalProperties: false
entry: plugin.py:Plugin
```

`auoreo plugin add <path|name> | remove | list | reload`; doctor verifies the lock hash and
entry point. Plugins never receive secrets they did not declare in `consumes`.

## Host calls available to a plugin (`HostAPI` in `auoreo_plugin_rt`)

| Call | Gate | What it does |
|---|---|---|
| `await host.secret(name)` | `consumes: ["secrets:NAME"]` (or a ≥3-char prefix glob like `secrets:MIIO_TOKEN_*`) | Resolve a secret from env / keyring / `secrets.db`; every call is audited |
| `await host.pairing_request(connector, chat_id, user)` | connector plugins | Allowlist check / pairing code |
| `host.message(connector, chat_id, user, text)` | `consumes: [sessions]` | Inbound chat message into a session |
| `await host.audit(action, detail)` | none (namespaced) | Audit row `plugin.<name>.<action>`; detail redacted, ≤ 4 KiB |
| `await host.usage(session_id, model, cost_usd)` | `consumes: [usage]` | Spend in the usage ledger (budgets, `auoreo stats`); ≤ $100 per call |
| `host.log(level, message)` | none | Plugin log line in the server log |
| `await host.oauth_token(connector)` | `consumes: ["oauth:<connector>"]` | Short-lived OAuth access token minted host side from the `oauth/<connector>` refresh token (`auoreo login <connector>`, `connectors/<name>/oauth.yaml`); in-memory TTL cache, audited `plugin.oauth`; the refresh token never reaches the plugin |

## Delivery `meta` keys a provider may receive

| Key | Meaning |
|---|---|
| `reply_to` | Connector-specific reply target (`message_id`, `thread_id`, `channel_id`, `interaction_token`, …) |
| `media` | Voice notes from a `MEDIA:` envelope in the reply: `[{path, mime, duration_s, kind}]` — Opus `.ogg` for telegram/discord/matrix/feishu/whatsapp/signal, MP3 elsewhere, ≤ 16 MiB, files under `<home>/sessions/` only (`auoreo.voice.notes`) |
| `stream` | `"partial"`: a streamed preview of a reply still being written (only with `plugins.<name>.stream: true`); the final text follows as a normal send |

