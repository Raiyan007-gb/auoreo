"""Discord delivery for AUOREO (stdlib + httpx). Outbound only; no connector in this wave.

Targets:
- `webhook` (or empty, when no default channel is configured) → `POST <webhook_url>?wait=true`
  with the URL from `secrets:discord/webhook_url`; `webhook:<thread_id>` posts into a thread.
- `<channel_id>`, `channel:<channel_id>` or `bot` (uses `default_channel_id`) →
  `POST /api/v10/channels/{id}/messages` with `Authorization: Bot <secrets:discord/bot_token>`.

Text is split into 2000-character messages; mentions are never parsed (`allowed_mentions`).
"""

from __future__ import annotations

from typing import Any

import httpx

API = "https://discord.com/api/v10"
CHUNK = 2000
NAME = "discord"
NO_MENTIONS = {"parse": []}


class DiscordError(RuntimeError):
    pass


def chunk(text: str, size: int = CHUNK) -> list[str]:
    out: list[str] = []
    rest = text
    while len(rest) > size:
        cut = rest.rfind("\n", 0, size)
        if cut <= 0:
            cut = size
        out.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest or not out:
        out.append(rest)
    return out


class Plugin:
    def __init__(self, host: Any, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.host = host
        self.transport = transport
        self.config: dict[str, Any] = {}
        self._client: httpx.AsyncClient | None = None

    def initialize(self, config: dict[str, Any], consumes_granted: list[str]) -> dict[str, Any]:
        self.config = dict(config)
        return {"tools": [], "deliveries": [NAME], "connectors": [], "skills": []}

    def config_update(self, config: dict[str, Any]) -> dict[str, Any]:
        self.config = dict(config)
        return {}

    async def shutdown(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(transport=self.transport, timeout=httpx.Timeout(15.0))
        return self._client

    async def _secret(self, name: str) -> str:
        value = await self.host.secret(name)
        if not value:
            raise DiscordError(f"secret {name} is not set")
        return str(value)

    def route(self, target: str) -> tuple[str, str | None]:
        """`("webhook", thread_id)` or `("bot", channel_id)`."""
        target = (target or "").strip()
        if target == "webhook" or target.startswith("webhook:"):
            return "webhook", target.partition(":")[2] or None
        if target.startswith("channel:"):
            return "bot", target.partition(":")[2]
        if target.isdigit():
            return "bot", target
        default = self.config.get("default_channel_id")
        if target in {"", "bot"} and default:
            return "bot", str(default)
        if target == "":
            return "webhook", None
        raise DiscordError(f"unknown target {target!r} (use webhook, webhook:<thread>, or a channel id)")

    async def _post(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        resp = await self._http().post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise DiscordError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else {}

    async def send(self, name: str, target: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
        parts = chunk(text or "(empty)")
        last: Any = None
        try:
            mode, ident = self.route(target)
            if mode == "webhook":
                url = await self._secret("discord/webhook_url")
                params = "?wait=true" + (f"&thread_id={ident}" if ident else "")
                extra = {"username": self.config["username"]} if self.config.get("username") else {}
                for part in parts:
                    payload = {"content": part, "allowed_mentions": NO_MENTIONS, **extra}
                    last = await self._post(url + params, payload, {})
            else:
                token = await self._secret("discord/bot_token")
                headers = {"Authorization": f"Bot {token}"}
                for part in parts:
                    payload = {"content": part, "allowed_mentions": NO_MENTIONS}
                    last = await self._post(f"{API}/channels/{ident}/messages", payload, headers)
        except (DiscordError, httpx.HTTPError) as exc:
            return {"ok": False, "detail": str(exc), "external_id": None}
        msg_id = last.get("id") if isinstance(last, dict) else None
        return {"ok": True, "detail": f"{len(parts)} message(s) via {mode}", "external_id": msg_id}
