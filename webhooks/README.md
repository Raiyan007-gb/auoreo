# webhooks/

`webhooks/<route>.yaml` exposes `POST /hooks/<route>` on the same port (GitHub routes also
answer on `POST /hooks/github/<route>`).

```yaml
name: github-pr               # route name
description: React to pull request events.
provider: github              # github | generic | gitlab | svix (see "Signatures")
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
challenge: false              # true: answer GET ?challenge=… and Slack url_verification
deliver_only: false           # true: send the rendered prompt to `deliver` now, no agent run
```

## Signatures

| provider | sender attaches | replay guard |
|---|---|---|
| `github`  | `X-Hub-Signature-256: sha256=<hex HMAC-SHA256(body)>`, `X-GitHub-Event`, `X-GitHub-Delivery` | delivery-id cache (GitHub sends no timestamp) |
| `generic` | `X-Timestamp: <unix seconds>`, `X-Signature-256: sha256=<hex HMAC-SHA256("<ts>.<raw body>")>`, `X-Delivery-Id`, event in body `event` or `X-Event` | ±300 s skew + delivery-id cache |
| `gitlab`  | `X-Gitlab-Token: <secret>` (constant-time compare), `X-Gitlab-Event`, `webhook-id` | delivery-id cache |
| `svix`    | `svix-id`, `svix-timestamp`, `svix-signature: v1,<b64 HMAC(key, "<id>.<ts>.<body>")> …` with a `whsec_<base64>` secret; any `v1` entry may match | ±300 s skew + `svix-id` cache |

A generic request without a parseable `X-Timestamp`, or outside ±300 s, is rejected (401).
`auoreo webhook test <route> -f payload.json` signs with the scheme of the route's provider.

## Secrets and rotation

The secret is resolved on every request (`auoreo secrets set webhook/<route>`, env or keyring).
`auoreo webhook rotate <route>` writes a new secret, prints it once, and moves the old one to
`webhook/<route>#prev`, which keeps verifying for 24 h (`webhooks.rotation_grace_s`); each use
of the previous secret is audited as `webhook.secret.rotated_used`.

An enabled route whose secret is missing answers **403**; startup logs a warning and writes a
`webhook.secret.missing` audit row, and `auoreo doctor` reports an error. Set
`{"webhooks": {"strict_secrets": true}}` in `settings.json` to refuse to start instead.

## Order of operations

body cap (413) → challenge (`challenge: true` only) → secret present (403) → signature on the
raw body, current then `#prev` (401) → JSON object (415 / 400) → event/filters (200 ignored) →
duplicate delivery id (200 duplicate) → rate limit (429) → enqueue → **202**. The agent runs
afterwards in its own non-interactive session; a `deliveries` row tracks it. `deliver_only`
routes deliver synchronously instead: **200** delivered, **502** when every target failed.
