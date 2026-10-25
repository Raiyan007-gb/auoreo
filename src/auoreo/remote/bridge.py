"""Instance bridge (spec P2): team mail across AUOREO instances.

Addressing (tool `send_message`):
- `bridge:<peer>/<member>` → HMAC-SHA256-signed `POST <peer url>/api/bridge/inbox`; the peer
  verifies with its own `bridges[<us>]` secret and delivers as `from: bridge:<us>/<member>`.
- `uds:<socket path>/<member>` → the same over a local Unix socket, unsigned: the socket's
  0600 permissions are the authentication. Delivered as `from: uds:<us>/<member>`.

Signature: `sha256=hex(HMAC(secret, f"{ts}.{nonce}.".encode() + body))` in
`X-Auoreo-Signature`, with `X-Auoreo-Timestamp` / `X-Auoreo-Nonce` mirrored in the body.
Replay protection: ±300 s timestamp window + a nonce cache (per process). Both sides audit.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import ValidationError

from auoreo.schema.bridge import BridgeDelivered, BridgeEnvelope

if TYPE_CHECKING:
    from auoreo.core.services import Services

SIG_HEADER = "x-auoreo-signature"
TS_HEADER = "x-auoreo-timestamp"
NONCE_HEADER = "x-auoreo-nonce"
WINDOW_S = 300
PREFIXES = ("bridge:", "uds:")
INBOX = "/api/bridge/inbox"
MAX_BODY = 256_000


class BridgeError(ValueError):
    """Sender-side refusal (unknown bridge, missing secret, peer said no)."""


class BridgeRefused(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def signature(secret: str, ts: int, nonce: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{ts}.{nonce}.".encode() + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def verify(secret: str, ts: int, nonce: str, body: bytes, header: str | None) -> bool:
    if not header or not header.startswith("sha256="):
        return False
    return hmac.compare_digest(signature(secret, ts, nonce, body).encode(), header.strip().encode())


class NonceCache:
    """Nonces seen inside the replay window (bounded; oldest evicted first)."""

    def __init__(self, window_s: int = WINDOW_S, cap: int = 100_000) -> None:
        self.window = window_s
        self.cap = cap
        self._seen: OrderedDict[str, float] = OrderedDict()

    def add(self, nonce: str, now: float | None = None) -> bool:
        """True if fresh (and now remembered); False if replayed."""
        now = time.time() if now is None else now
        while self._seen and (next(iter(self._seen.values())) < now or len(self._seen) >= self.cap):
            self._seen.popitem(last=False)
        if nonce in self._seen:
            return False
        self._seen[nonce] = now + 2 * self.window  # outlives any timestamp that could still pass
        return True


def parse_address(to: str) -> tuple[str, str, str]:
    """`bridge:b/worker` → (bridge, b, worker); `uds:/run/a.sock/lead` → (uds, /run/a.sock, lead)."""
    scheme, _, rest = to.partition(":")
    target, _, member = rest.rpartition("/")
    if scheme not in {"bridge", "uds"} or not target or not member:
        raise BridgeError(f"bad address '{to}': use bridge:<peer>/<member> or uds:<socket path>/<member>")
    return scheme, target, member


def _transport_url(url: str) -> tuple[httpx.AsyncBaseTransport | None, str]:
    if url.startswith("unix://"):
        return httpx.AsyncHTTPTransport(uds=url.removeprefix("unix://")), "http://auoreo"
    return None, url.rstrip("/")


async def send(
    svc: Services,
    *,
    sender_member: str,
    to: str,
    message: str,
    summary: str = "",
    session_id: str | None = None,
    team: str | None = None,
) -> BridgeDelivered:
    scheme, target, member = parse_address(to)
    headers = {"content-type": "application/json"}
    if scheme == "bridge":
        peer = svc.settings.bridges.get(target)
        if peer is None:
            known = ", ".join(sorted(svc.settings.bridges)) or "none configured"
            raise BridgeError(f"unknown bridge '{target}' (known: {known})")
        secret = svc.secrets.get(peer.secret.removeprefix("secret:"))
        if not secret:
            raise BridgeError(f"bridge '{target}': secret '{peer.secret}' is not set")
        instance, url = peer.as_name or svc.settings.instance_name, peer.url
    else:
        secret, instance, url = None, svc.settings.instance_name, f"unix://{target}"
    env = BridgeEnvelope(
        sender=instance,
        from_member=sender_member,
        to_member=member,
        team=team,
        message=message,
        summary=summary,
        nonce=secrets.token_urlsafe(18),
        timestamp=int(time.time()),
    )
    body = env.model_dump_json().encode()
    if secret is not None:
        headers[SIG_HEADER] = signature(secret, env.timestamp, env.nonce, body)
        headers[TS_HEADER] = str(env.timestamp)
        headers[NONCE_HEADER] = env.nonce
    transport, base = _transport_url(url)
    detail = {"to": to, "from": sender_member, "summary": summary, "nonce": env.nonce, "scheme": scheme}
    try:
        async with httpx.AsyncClient(transport=transport, timeout=15) as c:
            resp = await c.post(base + INBOX, content=body, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        await _audit(svc, "bridge.send", session_id, "error", str(exc), detail)
        raise BridgeError(f"cannot reach {to}: {exc}") from exc
    if resp.status_code != 200:
        reason = _reason(resp)
        await _audit(svc, "bridge.send", session_id, "deny", reason, detail)
        raise BridgeError(f"{to} refused the mail ({resp.status_code}): {reason}")
    delivered = BridgeDelivered.model_validate(resp.json())
    await _audit(svc, "bridge.send", session_id, "allow", "", {**detail, "mail_id": delivered.mail_id})
    return delivered


def _reason(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("detail", resp.text))
    except ValueError:
        return resp.text[:200]


async def _audit(
    svc: Services, action: str, session_id: str | None, decision: str, reason: str, detail: dict[str, Any]
) -> None:
    await svc.audit.record(
        actor="bridge", action=action, session_id=session_id, decision=decision, reason=reason, detail=detail
    )


def _authenticate(svc: Services, nonces: NonceCache, env: BridgeEnvelope, body: bytes, headers: Any) -> None:
    peer = svc.settings.bridges.get(env.sender)
    if peer is None:
        raise BridgeRefused(403, f"unknown bridge '{env.sender}'")
    secret = svc.secrets.get(peer.secret.removeprefix("secret:"))
    if not secret:
        raise BridgeRefused(403, f"bridge '{env.sender}' has no secret configured here")
    if headers.get(TS_HEADER) != str(env.timestamp) or headers.get(NONCE_HEADER) != env.nonce:
        raise BridgeRefused(401, "timestamp/nonce headers do not match the body")
    if not verify(secret, env.timestamp, env.nonce, body, headers.get(SIG_HEADER)):
        raise BridgeRefused(401, "bad signature")
    if abs(time.time() - env.timestamp) > WINDOW_S:
        raise BridgeRefused(401, f"timestamp outside the ±{WINDOW_S}s replay window")
    if not nonces.add(env.nonce):
        raise BridgeRefused(401, "replayed nonce")


async def _team_for(teams: Any, env: BridgeEnvelope) -> Any:
    if env.team:
        for t in await teams.list_teams():
            if env.team in {t.id, t.name}:
                return t
        raise BridgeRefused(404, f"no team '{env.team}' here")
    for t in reversed(await teams.list_teams()):  # newest team that has this member
        if env.to_member in {"lead", "*"} or await teams.member(t.id, env.to_member) is not None:
            return t
    raise BridgeRefused(404, f"no team here has a member named '{env.to_member}'")


async def receive(
    svc: Services, nonces: NonceCache, *, body: bytes, headers: Any, over_uds: bool
) -> BridgeDelivered:
    """Verify (unless over the Unix socket) and deliver into the local team mailbox."""
    detail: dict[str, Any] = {"over_uds": over_uds}
    try:
        if len(body) > MAX_BODY:
            raise BridgeRefused(413, "envelope too large")
        try:
            env = BridgeEnvelope.model_validate_json(body)
        except ValidationError as exc:
            raise BridgeRefused(422, f"bad envelope: {exc.error_count()} error(s)") from exc
        detail.update(sender=env.sender, from_member=env.from_member, to=env.to_member, nonce=env.nonce)
        if not over_uds:
            _authenticate(svc, nonces, env, body, headers)
        label = f"{'uds' if over_uds else 'bridge'}:{env.sender}/{env.from_member}"
        if len(label) > 64:
            raise BridgeRefused(422, "sender label longer than 64 characters")
        teams = svc.ext.get("teams")
        if teams is None:
            raise BridgeRefused(503, "teams are not running on this instance")
        team = await _team_for(teams, env)
        try:
            rows = await teams.mail.send(team.id, label, env.to_member, env.message, env.summary)
        except ValueError as exc:  # TeamsError: unknown member, broadcast rules, …
            raise BridgeRefused(404, str(exc)) from exc
    except BridgeRefused as exc:
        await _audit(svc, "bridge.receive", None, "deny", exc.reason, detail)
        raise
    session_id = await teams.session_for(team, rows[0].recipient) if rows else None
    await _audit(
        svc, "bridge.receive", session_id, "allow", "", {**detail, "team": team.id, "summary": env.summary}
    )
    first = rows[0] if rows else None
    return BridgeDelivered(
        team=team.id,
        recipient=", ".join(r.recipient for r in rows),
        sender=label,
        mail_id=first.id if first else 0,
    )
