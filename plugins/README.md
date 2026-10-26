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


## Trust tiers (I6, spec §25.15)

`bundled (signed) > allow-listed (pinned) > local-path (--trust-local) > URL (deny)`.

| Tier | How it gets here | Gate |
|---|---|---|
| bundled | ships in `plugins/` | ed25519 signature in `plugins/SIGNATURES.json` by a pinned key (`auoreo.plugins.signing.PLUGIN_PUBKEYS`); `i6.plugins.bundled_signatures: enforce\|warn\|off` (default `warn`) |
| allow-listed | `auoreo plugin pip-add auoreo-plugin-<x>[==ver]` | only `oreo-plugin-*` / `auoreo-plugin-*`, pinned by SHA-256 in `i6.plugins.allow_list` or a configured tap (`i6.plugins.taps`, signed rows verified); installed with `uv pip install --require-hashes` into the plugin's own venv; must still be allow-listed at every start |
| local-path | `auoreo plugin add <folder> --trust-local` | quarantined until `plugin verify` + `plugin enable` |
| URL | — | always denied |

`lock.json` records `{registry, version, sha256, signed_by, tier, venv_sha256}` (+ `package`
and `entry` for pip installs). `auoreo doctor` re-hashes every installed plugin folder and venv
(each `*.dist-info/RECORD` file hash) against the lock; the host quarantines a plugin whose venv
no longer matches. `auoreo plugin trust` shows tier/registry/signer/integrity, `auoreo plugin
search` lists the taps, `auoreo plugin entry-points` lists `importlib.metadata` entry points
(`auoreo.plugin`, `oreo.plugin`) of the server's own environment — listed only, never imported
or started (install them with `pip-add` so they get their own venv).

Re-sign the bundled plugins after editing one (release owner, private key never committed):
`python packaging/sign_plugins.py sign --key <pem>`; `python packaging/sign_plugins.py verify`.

A pip plugin's entry point is `package.module:Class` (the runtime imports it from the venv);
its `plugin.yaml` is taken from the package data (or synthesized) with `entry` set to it.

## Webhook providers (`provides: [webhook_provider]`)

A plugin can add a webhook provider kind for `webhooks/<route>.yaml` (`provider: <kind>`):
return `webhook_providers: ["<kind>"]` from `initialize` and implement

```python
def webhook_verify(self, kind, secret, headers, body: bytes, now) -> dict:
    return {"ok": True, "event": "push", "delivery_id": "abc"}  # or {"ok": False, "reason": "…"}


def webhook_parse(self, kind, payload, headers) -> dict:  # optional normalization
    return payload
```

Core keeps routing, body caps, rate limits, idempotency, sessions and delivery. Built-in kinds
(`github`, `gitlab`, `svix`, `generic`, `telegram-callback`) cannot be replaced; a route whose
provider is not running answers 503.

## Creator mode limits: `vmTimeoutMs`

`i6.creator.vm_timeout_ms` (default **5000**, `vmTimeoutMs` in the spec) bounds the
**synchronous** body of a creator tool (`def run(args, ctx)`): it runs under a POSIX interval
timer in the sandbox process and fails with `TimeoutError` when it overruns. An `async def run`
**escapes this bound** once it awaits — only the tool timeout (`plugins_tool_timeout_s`) and the
host's call timeout apply to async bodies. On platforms without `setitimer` (Windows) the bound
is not enforced.
