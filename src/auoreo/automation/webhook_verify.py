"""Webhook signature schemes, secret rotation and provider challenges (spec §5.1, §19.11).

| provider | proof | replay guard |
|---|---|---|
| github  | `X-Hub-Signature-256: sha256=<hex HMAC(body)>` | delivery-id cache (GitHub sends no timestamp) |
| generic | `X-Signature-256: sha256=<hex HMAC("<ts>.<body>")>` + `X-Timestamp` | ±300 s + id cache |
| gitlab  | `X-Gitlab-Token: <secret>` (constant-time compare) | delivery-id cache |
| svix    | `svix-signature: v1,<b64 HMAC("<id>.<ts>.<body>")> …` (`whsec_` key) | ±300 s + id cache |
| telegram-callback | `X-Telegram-Bot-Api-Secret-Token: <secret>` (constant-time) | `update_id` cache |

Providers (built in or from plugins) sit behind `webhook_providers.WebhookProvider`.

Every comparison is `hmac.compare_digest` on bytes. Rotation: `secret:webhook/<route>` is
current; `…#prev` still verifies for `webhooks.rotation_grace_s` (24 h) after
`…#rotated_at`, and each use of it is audited as `webhook.secret.rotated_used`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets as pysecrets
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal

from auoreo.harness.manifests import WebhookManifest

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.core.settings import Settings

TIMESTAMP_SKEW_S = 300
MAX_CHALLENGE = 512
SIGNATURE_HEADER = {
    "github": "x-hub-signature-256",
    "generic": "x-signature-256",
    "gitlab": "x-gitlab-token",
    "svix": "svix-signature",
    "telegram-callback": "x-telegram-bot-api-secret-token",
}
DELIVERY_HEADERS: dict[str, tuple[str, ...]] = {
    "github": ("x-github-delivery",),
    "generic": ("x-delivery-id",),
    "gitlab": ("webhook-id", "idempotency-key", "x-gitlab-event-uuid"),
    "svix": ("svix-id",),
    "telegram-callback": (),  # the payload's `update_id`
}
DELIVERY_HEADER = {p: h[0] for p, h in DELIVERY_HEADERS.items() if h}
EVENT_HEADER = {
    "github": "x-github-event",
    "generic": "x-event",
    "gitlab": "x-gitlab-event",
    "svix": "",
    "telegram-callback": "",
}

DoctorRow = tuple[str, Literal["ok", "warn", "error"], str, str]
Verifier = Callable[[str, Mapping[str, str], bytes, float], str | None]
"""(secret, lower-cased headers, raw body, now) → None when valid, else the reason."""


# ------------------------------------------------------------------ signing (senders + tests)
def sign(secret: str, body: bytes) -> str:
    """`sha256=<hex>` HMAC-SHA256 of the raw body (GitHub's `X-Hub-Signature-256`)."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def sign_v2(secret: str, body: bytes, timestamp: int) -> str:
    """Generic V2: `sha256=<hex HMAC-SHA256("<unix-sec>.<raw body>")>`."""
    return "sha256=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def svix_key(secret: str) -> bytes | None:
    """`whsec_<base64>` → key bytes (the prefix is optional)."""
    try:
        return base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (binascii.Error, ValueError):
        return None


def sign_svix(secret: str, msg_id: str, timestamp: int, body: bytes) -> str:
    key = svix_key(secret)
    if key is None:
        raise ValueError("svix secrets are whsec_<base64>")
    mac = hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def signed_headers(
    provider: str, secret: str, body: bytes, *, event: str, delivery_id: str, now: float | None = None
) -> dict[str, str]:
    """Everything a sender attaches for `provider` (used by `auoreo webhook test` and tests)."""
    ts = int(time.time() if now is None else now)
    headers = {"Content-Type": "application/json"}
    if provider in DELIVERY_HEADER:
        headers[DELIVERY_HEADER[provider]] = delivery_id
    if EVENT_HEADER.get(provider):
        headers[EVENT_HEADER[provider]] = event
    signer: dict[str, Callable[[], dict[str, str]]] = {
        "github": lambda: {"X-Hub-Signature-256": sign(secret, body)},
        "generic": lambda: {"X-Signature-256": sign_v2(secret, body, ts), "X-Timestamp": str(ts)},
        "gitlab": lambda: {"X-Gitlab-Token": secret},
        "svix": lambda: {
            "svix-timestamp": str(ts),
            "svix-signature": sign_svix(secret, delivery_id, ts, body),
        },
        "telegram-callback": lambda: {"X-Telegram-Bot-Api-Secret-Token": secret},
    }
    return headers | signer[provider]()


# ------------------------------------------------------------------ verification
def timestamp_ok(value: str | None, now: float) -> bool:
    """Unix seconds within ±300 s. Missing or unparseable → False (never 'skip the check')."""
    if value is None or not value.strip().isdigit():
        return False
    return abs(now - int(value.strip())) <= TIMESTAMP_SKEW_S


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def verify_github(secret: str, headers: Mapping[str, str], body: bytes, now: float) -> str | None:
    got = headers.get("x-hub-signature-256", "").strip()
    return None if got.startswith("sha256=") and _eq(sign(secret, body), got) else "bad signature"


def verify_generic(secret: str, headers: Mapping[str, str], body: bytes, now: float) -> str | None:
    ts = headers.get("x-timestamp")
    if not timestamp_ok(ts, now):
        return "missing, unparseable or stale timestamp"
    got = headers.get("x-signature-256", "").strip()
    assert ts is not None
    return None if got and _eq(sign_v2(secret, body, int(ts.strip())), got) else "bad signature"


def verify_gitlab(secret: str, headers: Mapping[str, str], body: bytes, now: float) -> str | None:
    got = headers.get("x-gitlab-token")
    return None if got and _eq(secret, got) else "bad token"


def verify_svix(secret: str, headers: Mapping[str, str], body: bytes, now: float) -> str | None:
    msg_id, ts = headers.get("svix-id"), headers.get("svix-timestamp")
    if not msg_id:
        return "missing svix-id"
    if not timestamp_ok(ts, now):
        return "missing, unparseable or stale timestamp"
    assert ts is not None
    try:
        expected = sign_svix(secret, msg_id, int(ts.strip()), body).removeprefix("v1,")
    except ValueError:
        return "route secret is not whsec_<base64>"
    for entry in headers.get("svix-signature", "").split():
        version, _, sig = entry.partition(",")
        if version == "v1" and sig and _eq(expected, sig):
            return None
    return "bad signature"


VERIFIERS: dict[str, Verifier] = {
    "github": verify_github,
    "generic": verify_generic,
    "gitlab": verify_gitlab,
    "svix": verify_svix,
}


# ------------------------------------------------------------------ secrets + rotation
def secret_name(manifest: WebhookManifest) -> str:
    return manifest.secret.removeprefix("secret:")


def prev_name(name: str) -> str:
    return f"{name}#prev"


def rotated_name(name: str) -> str:
    return f"{name}#rotated_at"


def candidates(
    get: Callable[[str], str | None], name: str, grace_s: int, now: float
) -> list[tuple[str, str]]:
    """[("current", s), ("prev", s)?] — `#prev` only within the grace window after rotation."""
    out: list[tuple[str, str]] = []
    if current := get(name):
        out.append(("current", current))
    prev, rotated = get(prev_name(name)), get(rotated_name(name))
    if prev and rotated and rotated.strip().isdigit() and now - int(rotated) <= grace_s:
        out.append(("prev", prev))
    return out


def new_secret(provider: str) -> str:
    if provider == "svix":
        return "whsec_" + base64.b64encode(pysecrets.token_bytes(32)).decode()
    return pysecrets.token_urlsafe(32)


def rotate(store: Any, name: str, provider: str, now: float | None = None) -> str:
    """New secret into `name`; the old one moves to `#prev` with the rotation time."""
    old = store.get(name)
    if old:
        store.set(prev_name(name), old)
        store.set(rotated_name(name), str(int(time.time() if now is None else now)))
    fresh = new_secret(provider)
    store.set(name, fresh)
    return fresh


def missing_secrets(routes: Mapping[str, WebhookManifest], get: Callable[[str], str | None]) -> list[str]:
    """Enabled routes whose current secret does not resolve (they answer 403)."""
    return sorted(n for n, m in routes.items() if m.enabled and not get(secret_name(m)))


def doctor_rows(settings: Settings, services: Services | None) -> list[DoctorRow]:
    """(check, level, detail, remedy) for `auoreo doctor`: an error per enabled route without
    a secret (the route answers 403; `webhooks.strict_secrets` refuses to start)."""
    if not settings.webhooks_enabled:
        return []
    if services is not None:
        harness, get = services.harness, services.secrets.get
    else:
        from pathlib import Path

        from auoreo.core.paths import bundled_root
        from auoreo.core.secrets import SecretStore
        from auoreo.harness.discovery import default_roots
        from auoreo.harness.registry import Harness

        harness = Harness(default_roots(settings.home, Path.cwd(), bundled_root(), settings.extra_roots))
        get = SecretStore(settings.home, settings.keyring).get
    routes = {n: d.manifest for n, d in harness.webhooks.items()}
    rows: list[DoctorRow] = [
        (
            f"webhook:{n}",
            "error",
            f"secret {routes[n].secret} is not set: POST /hooks/{n} answers 403",
            f"auoreo webhook rotate {n}  (or auoreo secrets set {secret_name(routes[n])})",
        )
        for n in missing_secrets(routes, get)
    ]
    enabled = [n for n, m in routes.items() if m.enabled]
    if enabled and not rows:
        rows.append(("webhooks", "ok", f"{len(enabled)} enabled route(s), all secrets set", ""))
    return rows


# ------------------------------------------------------------------ challenges
def query_challenge(query: Mapping[str, str]) -> str | None:
    """`GET /hooks/<route>?challenge=<v>` (also `hub.challenge`, `crc_token`-less generic echo)."""
    value = query.get("challenge") or query.get("hub.challenge")
    return value if value and len(value) <= MAX_CHALLENGE else None


def body_challenge(payload: Any) -> str | None:
    """Slack-style `{"type": "url_verification", "challenge": "<v>"}`."""
    if not isinstance(payload, dict) or payload.get("type") != "url_verification":
        return None
    value = payload.get("challenge")
    return value if isinstance(value, str) and 0 < len(value) <= MAX_CHALLENGE else None
