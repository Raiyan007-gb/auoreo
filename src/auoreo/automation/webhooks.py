"""Webhook ingress (spec §5.1, §19.11): `POST /hooks/<route>` → verify → filter → enqueue → 202.

Order: route (404) → body cap (413) → secret present (503, fail closed) → HMAC on the raw
body (401) → JSON content type / object (415 / 400) → event + filters (200 ignored) →
duplicate delivery id (200 duplicate) → rate limit (429) → enqueue → 202. Every decision is
audited without the body.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from auoreo.automation.deliveries import outbox_of
from auoreo.automation.templating import dotted, first_failing, render, untrusted
from auoreo.harness.manifests import WebhookManifest

if TYPE_CHECKING:
    from starlette.requests import Request

    from auoreo.core.services import Services

log = logging.getLogger(__name__)
TIMESTAMP_SKEW_S = 300
SIGNATURE_HEADER = {"github": "x-hub-signature-256", "generic": "x-signature-256"}
DELIVERY_HEADER = {"github": "x-github-delivery", "generic": "x-delivery-id"}


@dataclass
class HookResult:
    status_code: int
    body: dict[str, Any]
    reason: str = ""


class BodyTooLarge(Exception):
    pass


def sign(secret: str, body: bytes) -> str:
    """`sha256=<hex>` HMAC-SHA256 of the raw body (GitHub's `X-Hub-Signature-256` format)."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    return hmac.compare_digest(sign(secret, body).encode(), header.strip().encode())


def secret_name(manifest: WebhookManifest) -> str:
    return manifest.secret.removeprefix("secret:")


def event_name(manifest: WebhookManifest, headers: dict[str, str], payload: dict[str, Any]) -> str:
    if manifest.provider == "github":
        return headers.get("x-github-event", "")
    ev = payload.get("event")
    return ev if isinstance(ev, str) else headers.get("x-event", "")


def github_target(payload: dict[str, Any]) -> str:
    """`owner/repo#number` for issue / pull request events (used by `github_comment`)."""
    repo = dotted(payload, "repository.full_name")
    for path in ("pull_request.number", "issue.number", "number"):
        num = dotted(payload, path)
        if isinstance(repo, str) and isinstance(num, int):
            return f"{repo}#{num}"
    return ""


@dataclass
class WebhookIngress:
    services: Services
    _windows: dict[str, tuple[int, int]] = field(default_factory=dict)
    _warned: set[str] = field(default_factory=set)

    def routes(self) -> dict[str, WebhookManifest]:
        if not self.services.settings.webhooks_enabled:
            return {}
        return {n: d.manifest for n, d in self.services.harness.webhooks.items()}

    def enabled_routes(self) -> list[str]:
        return sorted(n for n, m in self.routes().items() if m.enabled)

    def _rate_ok(self, route: str, limit: int) -> bool:
        window = int(time.time() // 60)
        start, count = self._windows.get(route, (window, 0))
        if start != window:
            start, count = window, 0
        if count >= limit:
            return False
        self._windows[route] = (start, count + 1)
        return True

    async def _audit(self, route: str, res: HookResult, extra: dict[str, Any]) -> HookResult:
        accepted = res.status_code == 202
        await self.services.audit.record(
            actor="webhook",
            action="webhook.accepted" if accepted else "webhook.rejected",
            decision=str(res.body.get("status", res.status_code)),
            reason=res.reason,
            detail={"route": route[:64], "status_code": res.status_code, **extra},
        )
        return res

    async def receive(self, route: str, request: Request) -> HookResult:
        manifest = self.routes().get(route)
        if manifest is None or not manifest.enabled:
            return await self._audit(route, HookResult(404, {"detail": "not found"}, "unknown route"), {})
        try:
            body = await read_capped(request, manifest.max_body_bytes)
        except BodyTooLarge:
            return await self._audit(route, HookResult(413, {"detail": "payload too large"}, "body cap"), {})
        headers = {k.lower(): v for k, v in request.headers.items()}
        return await self.process(manifest, headers, body)

    async def process(self, manifest: WebhookManifest, headers: dict[str, str], body: bytes) -> HookResult:
        route = manifest.name
        secret = self.services.secrets.get(secret_name(manifest))
        if not secret:
            if route not in self._warned:
                self._warned.add(route)
                log.error("webhook route %s: secret %s is not set; rejecting (503)", route, manifest.secret)
            res = HookResult(503, {"detail": "webhook secret not configured"}, "missing secret")
            return await self._audit(route, res, {})
        if not verify(secret, body, headers.get(SIGNATURE_HEADER[manifest.provider])):
            return await self._audit(route, HookResult(401, {"detail": "bad signature"}, "bad signature"), {})
        if manifest.provider == "generic" and not _timestamp_ok(headers.get("x-timestamp")):
            return await self._audit(route, HookResult(401, {"detail": "stale timestamp"}, "timestamp"), {})
        ctype = headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/json" and not ctype.endswith("+json"):
            res = HookResult(415, {"detail": "content type must be application/json"}, "content type")
            return await self._audit(route, res, {})
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = None
        if not isinstance(payload, dict):
            res = HookResult(400, {"detail": "body must be a JSON object"}, "bad json")
            return await self._audit(route, res, {})
        return await self._accept(manifest, headers, body, payload)

    async def _accept(
        self, manifest: WebhookManifest, headers: dict[str, str], body: bytes, payload: dict[str, Any]
    ) -> HookResult:
        route = manifest.name
        event = event_name(manifest, headers, payload)
        action = payload.get("action")
        delivery_id = headers.get(DELIVERY_HEADER[manifest.provider]) or hashlib.sha256(body).hexdigest()
        info = {"event": event, "delivery_id": delivery_id[:128]}
        names = {event, f"{event}.{action}"} if isinstance(action, str) else {event}
        if manifest.events and not names & set(manifest.events):
            return await self._audit(route, HookResult(200, {"status": "ignored"}, f"event {event!r}"), info)
        failing = first_failing(manifest.filters, payload)
        if failing is not None:
            res = HookResult(200, {"status": "ignored"}, f"filter {failing.field}")
            return await self._audit(route, res, info)
        outbox = outbox_of(self.services)
        dup = HookResult(200, {"status": "duplicate"}, "duplicate delivery id")
        if await outbox.exists("webhook", route, delivery_id):
            return await self._audit(route, dup, info)
        if not self._rate_ok(route, manifest.rate_limit_per_minute):
            res = HookResult(429, {"detail": "rate limit exceeded"}, "rate limit")
            return await self._audit(route, res, info)
        row = await outbox.enqueue(
            "webhook",
            route,
            delivery_id,
            build_payload(manifest, payload, event, delivery_id),
            manifest.deliver[0] if manifest.deliver else "log",
        )
        if row is None:  # lost an insert race with the same delivery id
            return await self._audit(route, dup, info)
        body_out = {"status": "accepted", "route": route, "event": event, "delivery_id": row.id}
        return await self._audit(route, HookResult(202, body_out, "enqueued"), info | {"row": row.id})

    async def close(self) -> None:
        self._windows.clear()


def build_payload(
    manifest: WebhookManifest, payload: dict[str, Any], event: str, delivery_id: str
) -> dict[str, Any]:
    """What the worker needs: the rendered, envelope-wrapped prompt and where to deliver."""
    rendered = render(manifest.prompt, {"event": payload, "route": manifest.name, "delivery_id": delivery_id})
    return {
        "prompt": untrusted(f"webhook:{manifest.name}", rendered),
        "agent": manifest.agent,
        "workflow": manifest.workflow,
        "deliver": list(manifest.deliver),
        "route": manifest.name,
        "event": event,
        "action": payload.get("action") if isinstance(payload.get("action"), str) else None,
        "delivery_target": github_target(payload) if manifest.provider == "github" else "",
    }


def _timestamp_ok(value: str | None) -> bool:
    if value is None:
        return True
    try:
        ts = float(value)
    except ValueError:
        return False
    return abs(time.time() - ts) <= TIMESTAMP_SKEW_S


async def read_capped(request: Request, cap: int) -> bytes:
    """Reject on `Content-Length` before reading, then enforce the cap while streaming."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > cap:
                raise BodyTooLarge
        except ValueError as exc:
            raise BodyTooLarge from exc
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > cap:
            raise BodyTooLarge
    return bytes(buf)


async def start(services: Services) -> WebhookIngress:
    return WebhookIngress(services)
