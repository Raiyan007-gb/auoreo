"""Webhook ingress (spec §5.1, §19.11): `POST /hooks/<route>` → verify → filter → enqueue → 202.

Order: route (404) → IP allowlist (`allowed_cidrs`, 403) → body cap (413) → challenge
(`challenge: true` routes only, 200) → secret present (403, fail closed) → provider signature
on the raw body, current then `#prev` within the rotation grace (401) → JSON content type /
object (415 / 400) → `script:` + `transform` hooks (200 ignored on drop, 502 on failure) →
event + filters (200 ignored) → duplicate delivery id (200 duplicate) → rate limit (429) → enqueue → 202, or
for `deliver_only` routes deliver now → 200 / 502. Every decision is audited without the body.
Signature schemes live in `webhook_verify`, behind `webhook_providers.WebhookProvider` (built-in
kinds + plugin `webhook_provider`s; an unknown kind answers 503). `allowed_cidrs` may name the
`github` preset (`github_meta`).
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import IntegrityError

from auoreo.automation import github_meta  # --- I6: `allowed_cidrs: [github]` preset
from auoreo.automation import webhook_providers as providers  # --- I6
from auoreo.automation.deliveries import DeliveryWorker, outbox_of
from auoreo.automation.templating import dotted, first_failing, raw_text, render, untrusted
from auoreo.automation.webhook_extras import IGNORE, ScriptError, client_ip, ip_in, transform
from auoreo.automation.webhook_providers import Verdict, WebhookProvider
from auoreo.automation.webhook_verify import (
    DELIVERY_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_SKEW_S,
    body_challenge,
    candidates,
    missing_secrets,
    query_challenge,
    secret_name,
    sign,
    sign_v2,
)
from auoreo.core.db import DeliveryRow, utcnow
from auoreo.harness.manifests import WebhookManifest

if TYPE_CHECKING:
    from starlette.requests import Request

    from auoreo.core.services import Services

log = logging.getLogger(__name__)
__all__ = [
    "DELIVERY_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMP_SKEW_S",
    "HookResult",
    "WebhookIngress",
    "secret_name",
    "sign",
    "sign_v2",
]


@dataclass
class HookResult:
    status_code: int
    body: dict[str, Any]
    reason: str = ""


class BodyTooLarge(Exception):
    pass


def event_name(manifest: WebhookManifest, headers: Mapping[str, str], payload: dict[str, Any]) -> str:
    provider = providers.get(manifest.provider)
    return provider.event_name(headers, payload) if provider is not None else ""


def delivery_id_of(
    manifest: WebhookManifest, headers: Mapping[str, str], body: bytes, payload: dict[str, Any] | None = None
) -> str:
    provider = providers.get(manifest.provider)
    found = provider.delivery_id(headers, body, payload or {}) if provider is not None else None
    return found or hashlib.sha256(body).hexdigest()


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

    def route(self, name: str, provider: str | None = None) -> WebhookManifest | None:
        """An enabled route (optionally of one provider); unknown/disabled/mismatch → None."""
        m = self.routes().get(name)
        if m is None or not m.enabled or (provider is not None and m.provider != provider):
            return None
        return m

    def missing_secrets(self) -> list[str]:
        return missing_secrets(self.routes(), self.services.secrets.get)

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
        accepted = res.status_code == 202 or res.body.get("status") == "delivered"
        await self.services.audit.record(
            actor="webhook",
            action="webhook.accepted" if accepted else "webhook.rejected",
            decision=str(res.body.get("status", res.status_code)),
            reason=res.reason,
            detail={"route": route[:64], "status_code": res.status_code, **extra},
        )
        return res

    async def challenge(self, route: str, query: Mapping[str, str]) -> HookResult:
        """`GET /hooks/<route>?challenge=<v>`: echo for `challenge: true` routes only."""
        manifest = self.route(route)
        if manifest is None:
            return HookResult(404, {"detail": "not found"}, "unknown route")
        value = query_challenge(query) if manifest.challenge else None
        if value is None:
            return HookResult(405, {"detail": "method not allowed"}, "non-POST")
        return await self._audit(route, HookResult(200, {"challenge": value}, "challenge"), {})

    async def receive(self, route: str, request: Request, provider: str | None = None) -> HookResult:
        manifest = self.route(route, provider)
        if manifest is None:
            return await self._audit(route, HookResult(404, {"detail": "not found"}, "unknown route"), {})
        if manifest.allowed_cidrs:
            trusted = self.services.settings.teams2.webhook_trusted_proxies
            ip = client_ip(request.client.host if request.client else None, request.headers, trusted)
            allowed = await github_meta.expand(manifest.allowed_cidrs, self.services.settings)
            if not ip_in(ip, allowed):
                res = HookResult(403, {"detail": "forbidden"}, "ip not allowed")
                return await self._audit(route, res, {"ip": ip[:64]})
        try:
            body = await read_capped(request, manifest.max_body_bytes)
        except BodyTooLarge:
            return await self._audit(route, HookResult(413, {"detail": "payload too large"}, "body cap"), {})
        headers = {k.lower(): v for k, v in request.headers.items()}
        return await self.process(manifest, headers, body)

    async def _verify(
        self, manifest: WebhookManifest, headers: dict[str, str], body: bytes
    ) -> HookResult | Verdict:
        route = manifest.name
        now = time.time()
        provider = providers.get(manifest.provider)
        if provider is None:
            res = HookResult(
                503, {"detail": "webhook provider unavailable"}, f"no provider {manifest.provider}"
            )
            return await self._audit(route, res, {})
        grace = self.services.settings.webhooks.rotation_grace_s
        keys = candidates(self.services.secrets.get, secret_name(manifest), grace, now)
        if not keys:
            if route not in self._warned:
                self._warned.add(route)
                log.error("webhook route %s: secret %s is not set; rejecting (403)", route, manifest.secret)
            return await self._audit(route, HookResult(403, {"detail": "forbidden"}, "missing secret"), {})
        reason = "bad signature"
        for which, secret in keys:
            verdict = await _safe_verify(provider, secret, headers, body, now)
            if verdict.ok:
                if which == "prev":
                    await self.services.audit.record(
                        actor="webhook",
                        action="webhook.secret.rotated_used",
                        decision="allow",
                        reason="previous secret within the rotation grace",
                        detail={"route": route[:64]},
                    )
                return verdict
            reason = verdict.reason or reason if which == "current" else reason
        if "timestamp" in reason:
            log.warning("webhook route %s: %s", route, reason)
        return await self._audit(route, HookResult(401, {"detail": reason}, reason), {})

    async def process(self, manifest: WebhookManifest, headers: dict[str, str], body: bytes) -> HookResult:
        route = manifest.name
        payload = _json_object(body)
        if manifest.challenge and (value := body_challenge(payload)) is not None:
            return await self._audit(route, HookResult(200, {"challenge": value}, "challenge"), {})
        verdict = await self._verify(manifest, headers, body)
        if isinstance(verdict, HookResult):
            return verdict
        ctype = headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype != "application/json" and not ctype.endswith("+json"):
            res = HookResult(415, {"detail": "content type must be application/json"}, "content type")
            return await self._audit(route, res, {})
        if payload is None:
            res = HookResult(400, {"detail": "body must be a JSON object"}, "bad json")
            return await self._audit(route, res, {})
        return await self._accept(manifest, headers, body, payload, verdict)

    async def _accept(
        self,
        manifest: WebhookManifest,
        headers: dict[str, str],
        body: bytes,
        payload: dict[str, Any],
        verdict: Verdict | None = None,
    ) -> HookResult:
        route = manifest.name
        provider = providers.get(manifest.provider)
        if provider is not None:  # optional normalize (a plugin provider answers over RPC)
            parsed = provider.parse(payload, headers)
            payload = await parsed if inspect.isawaitable(parsed) else parsed
        try:
            changed = await transform(self.services, manifest, payload)
        except ScriptError as exc:
            log.warning("webhook route %s: transform failed: %s", route, exc)
            return await self._audit(
                route, HookResult(502, {"detail": "transform failed"}, str(exc)[:300]), {}
            )
        if changed is IGNORE:
            return await self._audit(
                route, HookResult(200, {"status": "ignored"}, "dropped by transform"), {}
            )
        payload = changed
        event = (verdict.event if verdict else None) or event_name(manifest, headers, payload)
        action = payload.get("action")
        delivery_id = (verdict.delivery_id if verdict else None) or delivery_id_of(
            manifest, headers, body, payload
        )
        info = {"event": event, "delivery_id": delivery_id[:128]}
        names = {event, f"{event}.{action}"} if isinstance(action, str) else {event}
        if manifest.events and not names & set(manifest.events):
            return await self._audit(route, HookResult(200, {"status": "ignored"}, f"event {event!r}"), info)
        failing = first_failing(manifest.filters, payload, self.services.settings.home / "webhooks")
        if failing is not None:
            res = HookResult(200, {"status": "ignored"}, f"filter {failing.label}")
            return await self._audit(route, res, info)
        outbox = outbox_of(self.services)
        dup = HookResult(200, {"status": "duplicate"}, "duplicate delivery id")
        if await outbox.exists("webhook", route, delivery_id):
            return await self._audit(route, dup, info)
        if not self._rate_ok(route, manifest.rate_limit_per_minute):
            res = HookResult(429, {"detail": "rate limit exceeded"}, "rate limit")
            return await self._audit(route, res, info)
        built = build_payload(manifest, payload, event, delivery_id, raw_text(body))
        if manifest.deliver_only:
            return await self._deliver_now(manifest, payload, built, info | {"raw": raw_text(body)})
        row = await outbox.enqueue(
            "webhook", route, delivery_id, built, manifest.deliver[0] if manifest.deliver else "log"
        )
        if row is None:  # lost an insert race with the same delivery id
            return await self._audit(route, dup, info)
        body_out = {"status": "accepted", "route": route, "event": event, "delivery_id": row.id}
        return await self._audit(route, HookResult(202, body_out, "enqueued"), info | {"row": row.id})

    async def _deliver_now(
        self, manifest: WebhookManifest, payload: dict[str, Any], built: dict[str, Any], info: dict[str, Any]
    ) -> HookResult:
        """`deliver_only`: the row is born `delivering` (the worker never claims it, so no agent
        runs); the rendered template goes to the targets in order. 200 delivered / 502."""
        route, svc = manifest.name, self.services
        values = {
            "event": payload,
            "route": route,
            "delivery_id": info["delivery_id"],
            "__raw__": info.pop("raw"),
        }
        text = render(manifest.prompt, values)
        row = DeliveryRow(
            kind="webhook",
            source=route,
            external_id=str(info["delivery_id"]),
            payload=built | {"deliver_only": True},
            result={},
            target=(manifest.deliver[0] if manifest.deliver else "log")[:64],
            status="delivering",
            attempts=1,
            claimed_at=utcnow(),
        )
        try:
            async with svc.db.session() as s:
                s.add(row)
                await s.commit()
        except IntegrityError:
            dup = HookResult(200, {"status": "duplicate"}, "duplicate delivery id")
            return await self._audit(route, dup, info)
        meta = {"kind": "webhook", "source": route, "external_id": row.external_id, "delivery_id": row.id}
        provider, attempts = await DeliveryWorker(svc)._deliver(row, text, meta)
        error = None
        if provider is None:
            error = "; ".join(f"{a['provider']}: {a['detail']}" for a in attempts) or "no delivery target"
        await outbox_of(svc).finish(
            row.id, error is None, {"provider": provider, "attempts": attempts}, error
        )
        status = "delivered" if error is None else "failed"
        extra = {"provider": provider} | ({"error": error} if error else {})
        await svc.emit("server", f"delivery.{status}", meta | extra)
        out = {"status": status, "route": route, "event": built["event"], "delivery_id": row.id}
        if error is not None:
            res = HookResult(502, out | {"detail": error[:300]}, "deliver failed")
            return await self._audit(route, res, info)
        return await self._audit(route, HookResult(200, out, "delivered"), info | {"row": row.id})

    async def close(self) -> None:
        self._windows.clear()


async def _safe_verify(
    provider: WebhookProvider, secret: str, headers: dict[str, str], body: bytes, now: float
) -> Verdict:
    """A provider that raises (a crashed plugin) rejects; it never 500s the route."""
    try:
        return await provider.verify(secret, headers, body, now)
    except Exception as exc:
        log.warning("webhook provider %s failed to verify: %s", provider.kind, exc)
        return Verdict(False, "provider error")


def _json_object(body: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def build_payload(
    manifest: WebhookManifest, payload: dict[str, Any], event: str, delivery_id: str, raw: str = ""
) -> dict[str, Any]:
    """What the worker needs: the rendered, envelope-wrapped prompt and where to deliver."""
    values = {"event": payload, "route": manifest.name, "delivery_id": delivery_id, "__raw__": raw}
    rendered = render(manifest.prompt, values)
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
    """Warn (log + audit row) per enabled route whose secret is missing; such a route answers
    403. `webhooks.strict_secrets` turns this into a refusal to start (see `enforce_secrets`)."""
    ing = WebhookIngress(services)
    for route in ing.missing_secrets():
        manifest = ing.routes()[route]
        log.warning("webhook route %s: secret %s is not set; it will answer 403", route, manifest.secret)
        await services.audit.record(
            actor="webhook",
            action="webhook.secret.missing",
            decision="warn",
            reason="secret not set at startup",
            detail={"route": route[:64]},
        )
    return ing


def enforce_secrets(services: Services) -> None:
    """Startup gate: with `webhooks.strict_secrets`, a missing route secret refuses to start."""
    if not services.settings.webhooks.strict_secrets:
        return
    missing = WebhookIngress(services).missing_secrets()
    if missing:
        raise RuntimeError(
            f"webhooks.strict_secrets: route secret(s) missing for {', '.join(missing)} "
            "(auoreo webhook rotate <route>)"
        )
