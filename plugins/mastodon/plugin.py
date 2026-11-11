"""Mastodon connector + consent-gated delivery for AUOREO (stdlib + httpx).

Inbound (read only): every `poll_interval_s` (minimum 60) it reads
`GET /api/v1/notifications?types[]=mention&min_id=<cursor>` with the bot's access token. The
cursor and a per-UTC-day request counter live in the host's plugin state (`state.get` /
`state.set`), so a restart never replays mentions; the first poll only records the cursor.
Polling stops for the day at `daily_read_cap` and pauses for a rate-limit window when
`X-RateLimit-Remaining` reads 0. A mention from an account (`user@host`) that is not on the
allowlist gets a pairing code issued by the host and NO reply (the operator sees the code);
a paired mention becomes a session message whose draft answer the host turns into a `post`
suggestion.

Outbound: `POST /api/v1/statuses` only with `meta.consent_id` (the id of the accepted
suggestion, set by the host; a send without it is refused and audited). The consent id is the
`Idempotency-Key` (Mastodon keeps keys for up to an hour), the reply mentions the author and
answers the status in `reply_to.status_id`. Over `max_chars` the post is refused, never trimmed.

Network: only through the host's egress proxy (`net:proxy`; a host-minted token arrives in
`initialize`, never in the environment). Without a proxy the plugin makes no request. HTTPS
to the one configured instance, no redirects, no environment proxies. The access token is
sent as a header and never logged; errors name the endpoint and the HTTP status only.
"""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

NAME = "mastodon"
PAGE = 40
RATE_PAUSE_S = 300.0  # the documented rate-limit window is 5 minutes
_TAGS = re.compile(r"<[^>]+>")
_BREAKS = re.compile(r"<\s*(br|/p)\s*/?>", re.IGNORECASE)


class MastodonError(RuntimeError):
    pass


def plain(content: str) -> str:
    """Status HTML → plain text (line breaks kept, tags dropped, entities decoded)."""
    text = _TAGS.sub("", _BREAKS.sub("\n", content or ""))
    return "\n".join(" ".join(line.split()) for line in html.unescape(text).splitlines()).strip()


def newest(ids: list[str]) -> str:
    """The newest id: numeric order when every id is a number, else the first (newest-first)."""
    if all(i.isdigit() for i in ids):
        return max(ids, key=int)
    return ids[0]


def today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class Plugin:
    def __init__(self, host: Any, transport: httpx.AsyncBaseTransport | None = None) -> None:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        self.host = host
        self.transport = transport
        self.config: dict[str, Any] = {}
        self.allowlist: set[str] = set()
        self.state: dict[str, Any] = {}
        self.paused_until = 0.0
        self._token: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._poll: asyncio.Task[None] | None = None

    # ------------------------------------------------------------- protocol
    def initialize(self, config: dict[str, Any], consumes_granted: list[str]) -> dict[str, Any]:
        self.config_update(config)
        return {"tools": [], "deliveries": [NAME], "connectors": [NAME], "skills": []}

    def config_update(self, config: dict[str, Any]) -> dict[str, Any]:
        self.config = dict(config)
        self.allowlist = {str(c) for c in config.get("allowlist", [])}
        return {}

    async def connector_start(self, name: str) -> dict[str, Any]:
        if self._poll is None or self._poll.done():
            self.state = await self.host.state_get()
            self._poll = asyncio.create_task(self._poll_loop())
        return {}

    async def connector_stop(self, name: str) -> dict[str, Any]:
        if self._poll is not None:
            self._poll.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll
            self._poll = None
        return {}

    async def shutdown(self) -> None:
        await self.connector_stop(NAME)
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ API
    @property
    def instance(self) -> str:
        host = str(self.config.get("instance", "")).strip().lower()
        if not host or "/" in host or ":" in host:
            raise MastodonError("config `instance` must be a bare host name")
        return host

    def client_options(self) -> dict[str, Any]:
        """httpx options: the egress proxy (required), no env proxies, no redirects."""
        proxy = getattr(self.host, "proxy", None)
        if not proxy:
            raise MastodonError("no egress proxy from the host (enable i9.egress); not connecting")
        opts: dict[str, Any] = {"trust_env": False, "follow_redirects": False, "timeout": 20.0}
        if self.transport is not None:
            opts["transport"] = self.transport  # tests: a mock transport stands in for the proxy
        else:
            opts["proxy"] = proxy
        return opts

    async def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        if self._client is None:
            self._client = httpx.AsyncClient(**self.client_options())
        if self._token is None:
            self._token = await self.host.secret("mastodon/access_token")
            if not self._token:
                raise MastodonError("secret mastodon/access_token is not set")
        headers = {"Authorization": f"Bearer {self._token}", **kw.pop("headers", {})}
        try:
            resp = await self._client.request(method, f"https://{self.instance}{path}", headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise MastodonError(f"{method} {path}: {type(exc).__name__}") from None
        if resp.status_code >= 300:
            raise MastodonError(f"{method} {path}: HTTP {resp.status_code}")
        return resp

    # ------------------------------------------------------------- inbound
    async def poll_once(self) -> int:
        """One notification read (if the day's cap and the rate limit allow) → mentions handled."""
        if time.monotonic() < self.paused_until:
            return 0
        if self.state.get("day") != today():
            self.state.update(day=today(), reads=0)
        if int(self.state.get("reads", 0)) >= int(self.config.get("daily_read_cap", 200)):
            return 0
        params: dict[str, Any] = {"types[]": "mention", "limit": PAGE}
        cursor = self.state.get("cursor")
        if cursor:
            params["min_id"] = str(cursor)
        self.state["reads"] = int(self.state.get("reads", 0)) + 1
        try:
            resp = await self._request("GET", "/api/v1/notifications", params=params)
        finally:
            await self.host.state_set(self.state)
        if resp.headers.get("X-RateLimit-Remaining", "").strip() == "0":
            self.paused_until = time.monotonic() + RATE_PAUSE_S
        items = [n for n in resp.json() or [] if isinstance(n, dict) and n.get("id") is not None]
        if not items:
            return 0
        self.state["cursor"] = newest([str(n["id"]) for n in items])
        await self.host.state_set(self.state)
        if not cursor:
            return 0  # first contact: start from now, never answer the backlog
        handled = 0
        for note in sorted(items, key=lambda n: int(n["id"]) if str(n["id"]).isdigit() else 0):
            handled += await self.handle_mention(note)
        return handled

    async def handle_mention(self, note: dict[str, Any]) -> int:
        status, account = note.get("status"), note.get("account")
        if note.get("type") != "mention" or not isinstance(status, dict) or not isinstance(account, dict):
            return 0
        acct = str(account.get("acct", ""))
        chat_id = acct if "@" in acct else f"{acct}@{self.instance}"
        text = plain(str(status.get("content", "")))
        if not acct or not text:
            return 0
        if chat_id not in self.allowlist:
            res = await self.host.pairing_request(NAME, chat_id, acct)
            if not res.get("allowed"):
                self.host.log("info", f"mastodon: mention from unpaired {chat_id}; pairing code issued")
                return 0  # never reply to an unpaired account in public
            self.allowlist.add(chat_id)
        reply_to = {"status_id": str(status.get("id", "")), "visibility": str(status.get("visibility", ""))}
        self.host.message(NAME, chat_id, acct, text, reply_to=reply_to)
        return 1

    async def _poll_loop(self) -> None:
        interval = max(60.0, float(self.config.get("poll_interval_s", 120)))
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.host.log("warning", f"mastodon poll failed: {exc}")
            await asyncio.sleep(interval)

    # ------------------------------------------------------------ outbound
    async def send(self, name: str, target: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
        consent_id = str(meta.get("consent_id") or "")
        if not consent_id:
            with contextlib.suppress(Exception):
                await self.host.audit("post", {"target": target[:64]}, "deny")
            return {"ok": False, "detail": "refused: no consent_id", "external_id": None}
        limit = int(self.config.get("max_chars", 500))
        reply_to = meta.get("reply_to") if isinstance(meta.get("reply_to"), dict) else {}
        body = f"@{target} {text}"
        if len(body) > limit:
            return {"ok": False, "detail": f"post is {len(body)} characters; the limit is {limit}"}
        form = {"status": body, "visibility": str(self.config.get("visibility", "unlisted"))}
        if reply_to.get("status_id"):
            form["in_reply_to_id"] = str(reply_to["status_id"])
        try:
            resp = await self._request(
                "POST", "/api/v1/statuses", data=form, headers={"Idempotency-Key": f"auoreo-{consent_id}"}
            )
            posted = resp.json()
        except (MastodonError, ValueError) as exc:
            return {"ok": False, "detail": str(exc), "external_id": None}
        status_id = str(posted.get("id")) if isinstance(posted, dict) else None
        with contextlib.suppress(Exception):
            await self.host.audit("post", {"consent_id": consent_id, "status_id": status_id}, "allow")
        return {"ok": True, "detail": "posted", "external_id": status_id}
