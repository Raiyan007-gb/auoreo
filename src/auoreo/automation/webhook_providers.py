"""`WebhookProvider` interface (spec §15 L650-659, §19.11, §24.8).

Core owns routing, body caps, HMAC dispatch, rate limits, idempotency, the queue, sessions and
delivery; a provider owns header names, the verify math, event/delivery-id extraction and an
optional payload normalization:

    class WebhookProvider(Protocol):
        kind: str
        async def verify(self, secret, headers, body, now) -> Verdict   # identity/event
        def event_name(self, headers, payload) -> str
        def delivery_id(self, headers, body, payload) -> str | None
        def parse(self, payload, headers) -> dict | Awaitable[dict]     # optional normalize

Built in: `github`, `gitlab`, `svix`, `generic` (math in `webhook_verify`) and
`telegram-callback` (`X-Telegram-Bot-Api-Secret-Token`, the `secret_token` given to
`setWebhook`). Plugins add more with `provides: [webhook_provider]` and advertise
`webhook_providers: [kind]` at `initialize`; the host registers a proxy that forwards
`webhook.verify` / `webhook.parse` over JSON-RPC (`plugins/webhook_bridge.py`). A plugin can
never replace a built-in kind.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from auoreo.automation.webhook_verify import (
    DELIVERY_HEADERS,
    EVENT_HEADER,
    Verifier,
    verify_generic,
    verify_github,
    verify_gitlab,
    verify_svix,
)

TELEGRAM_HEADER = "x-telegram-bot-api-secret-token"
BUILTIN_KINDS = ("github", "gitlab", "svix", "generic", "telegram-callback")


@dataclass(frozen=True)
class Verdict:
    """Outcome of `verify`: `ok` plus whatever identity the provider learned on the way."""

    ok: bool
    reason: str = ""
    event: str | None = None
    delivery_id: str | None = None
    identity: str | None = None


@runtime_checkable
class WebhookProvider(Protocol):
    @property
    def kind(self) -> str: ...

    async def verify(self, secret: str, headers: Mapping[str, str], body: bytes, now: float) -> Verdict: ...

    def event_name(self, headers: Mapping[str, str], payload: dict[str, Any]) -> str: ...

    def delivery_id(self, headers: Mapping[str, str], body: bytes, payload: dict[str, Any]) -> str | None: ...

    def parse(
        self, payload: dict[str, Any], headers: Mapping[str, str]
    ) -> dict[str, Any] | Awaitable[dict[str, Any]]: ...


def verify_telegram(secret: str, headers: Mapping[str, str], body: bytes, now: float) -> str | None:
    got = headers.get(TELEGRAM_HEADER)
    return None if got and hmac.compare_digest(secret.encode(), got.encode()) else "bad secret token"


@dataclass(frozen=True)
class HeaderProvider:
    """A provider fully described by a verifier, header names and a payload event key."""

    kind: str
    verifier: Verifier
    delivery_headers: tuple[str, ...] = ()
    event_header: str = ""
    payload_event_key: str = "event"
    payload_id: Callable[[dict[str, Any]], str | None] = field(default=lambda p: None)

    async def verify(self, secret: str, headers: Mapping[str, str], body: bytes, now: float) -> Verdict:
        failed = self.verifier(secret, headers, body, now)
        return Verdict(failed is None, failed or "")

    def event_name(self, headers: Mapping[str, str], payload: dict[str, Any]) -> str:
        if self.event_header and (value := headers.get(self.event_header)):
            return value
        ev = payload.get(self.payload_event_key) if self.payload_event_key else None
        return ev if isinstance(ev, str) else headers.get("x-event", "")

    def delivery_id(self, headers: Mapping[str, str], body: bytes, payload: dict[str, Any]) -> str | None:
        for name in self.delivery_headers:
            if value := headers.get(name):
                return value
        return self.payload_id(payload)

    def parse(self, payload: dict[str, Any], headers: Mapping[str, str]) -> dict[str, Any]:
        return payload


class TelegramCallback(HeaderProvider):
    """Telegram `setWebhook(secret_token=…)`: event = the update kind (`message`, …)."""

    def event_name(self, headers: Mapping[str, str], payload: dict[str, Any]) -> str:
        return next((k for k in payload if k != "update_id"), "")


def _update_id(payload: dict[str, Any]) -> str | None:
    uid = payload.get("update_id")
    return f"tg-{uid}" if isinstance(uid, int) else None


BUILTINS: dict[str, WebhookProvider] = {
    "github": HeaderProvider("github", verify_github, DELIVERY_HEADERS["github"], EVENT_HEADER["github"]),
    "gitlab": HeaderProvider("gitlab", verify_gitlab, DELIVERY_HEADERS["gitlab"], EVENT_HEADER["gitlab"]),
    "svix": HeaderProvider("svix", verify_svix, DELIVERY_HEADERS["svix"], payload_event_key="type"),
    "generic": HeaderProvider("generic", verify_generic, DELIVERY_HEADERS["generic"]),
    "telegram-callback": TelegramCallback("telegram-callback", verify_telegram, (), payload_id=_update_id),
}
_plugins: dict[str, tuple[str, WebhookProvider]] = {}


def register(provider: WebhookProvider, owner: str) -> str | None:
    """Add a plugin provider; returns a refusal reason (built-in or foreign kind) or None."""
    if provider.kind in BUILTINS:
        return f"webhook provider '{provider.kind}' is built in"
    current = _plugins.get(provider.kind)
    if current is not None and current[0] != owner:
        return f"webhook provider '{provider.kind}' is already provided by {current[0]}"
    _plugins[provider.kind] = (owner, provider)
    return None


def unregister(kind: str, owner: str) -> None:
    if (current := _plugins.get(kind)) is not None and current[0] == owner:
        del _plugins[kind]


def get(kind: str) -> WebhookProvider | None:
    found = BUILTINS.get(kind)
    if found is not None:
        return found
    entry = _plugins.get(kind)
    return entry[1] if entry else None


def kinds() -> dict[str, str]:
    """`kind → owner` (`builtin` or `plugin:<name>`)."""
    return {**dict.fromkeys(BUILTINS, "builtin"), **{k: f"plugin:{o}" for k, (o, _) in _plugins.items()}}


__all__ = [
    "BUILTINS",
    "BUILTIN_KINDS",
    "TELEGRAM_HEADER",
    "HeaderProvider",
    "Verdict",
    "WebhookProvider",
    "get",
    "kinds",
    "register",
    "unregister",
    "verify_telegram",
]
