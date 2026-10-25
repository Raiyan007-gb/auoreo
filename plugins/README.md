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
