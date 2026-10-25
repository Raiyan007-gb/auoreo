# webhooks/

`webhooks/<route>.yaml` exposes `POST /hooks/<route>` on the same port.

```yaml
name: github-pr               # route name
description: React to pull request events.
provider: github              # github (X-Hub-Signature-256 HMAC) | generic (X-Signature HMAC)
secret: "secret:webhook/github-pr"   # reference into the secret store, never a value
events: [pull_request]        # provider event names; empty = all
filters:
  - {field: action, equals: opened}
agent: reviewer
prompt: |
  A pull request was {{event.action}}: {{event.pull_request.title}}
  {{event.pull_request.html_url}}
deliver: [log]
rate_limit_per_minute: 30
max_body_bytes: 1048576
```

Order of operations: body cap (413) → HMAC on the raw body (401) → event/filters (200
ignored) → duplicate delivery id within 1h (200 duplicate) → enqueue → **202**. The agent
runs afterwards in its own non-interactive session; a `deliveries` row tracks it.
