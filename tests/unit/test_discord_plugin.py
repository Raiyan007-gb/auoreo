"""Discord plugin, in process, against mocked webhook / bot endpoints (`httpx.MockTransport`)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "discord" / "plugin.py"
WEBHOOK = "https://discord.com/api/webhooks/111/tok"
SECRETS = {"discord/webhook_url": WEBHOOK, "discord/bot_token": "bot-tok"}


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("dc_plugin_under_test", PLUGIN)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeHost:
    def __init__(self, secrets: dict[str, str]) -> None:
        self.secrets = secrets

    async def secret(self, name: str) -> str | None:
        return self.secrets.get(name)


def make(config: dict[str, Any] | None = None, secrets: dict[str, str] | None = None) -> tuple[Any, list]:  # type: ignore[type-arg]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"id": f"m{len(seen)}"})

    p = load().Plugin(
        FakeHost(SECRETS if secrets is None else secrets), transport=httpx.MockTransport(handler)
    )
    info = p.initialize(config or {}, [])
    assert info == {"tools": [], "deliveries": ["discord"], "connectors": ["discord"], "skills": []}
    return p, seen


async def test_webhook_send_chunks_at_2000() -> None:
    p, seen = make({"username": "auoreo"})
    receipt = await p.send("discord", "webhook", "a" * 4500, {})
    assert receipt == {"ok": True, "detail": "3 message(s) via webhook", "external_id": "m3"}
    assert [str(r.url) for r in seen] == [WEBHOOK + "?wait=true"] * 3
    bodies = [json.loads(r.content) for r in seen]
    assert [len(b["content"]) for b in bodies] == [2000, 2000, 500]
    assert bodies[0]["allowed_mentions"] == {"parse": []} and bodies[0]["username"] == "auoreo"
    assert "authorization" not in seen[0].headers


async def test_webhook_thread_target() -> None:
    p, seen = make()
    await p.send("discord", "webhook:999", "hi", {})
    assert seen[0].url.params["thread_id"] == "999"


async def test_bot_send_to_channel() -> None:
    p, seen = make()
    receipt = await p.send("discord", "channel:123456", "hello", {})
    assert receipt["ok"] and receipt["detail"] == "1 message(s) via bot"
    assert str(seen[0].url) == "https://discord.com/api/v10/channels/123456/messages"
    assert seen[0].headers["authorization"] == "Bot bot-tok"
    assert json.loads(seen[0].content) == {"content": "hello", "allowed_mentions": {"parse": []}}


async def test_bot_default_channel_and_bare_ids() -> None:
    p, seen = make({"default_channel_id": "777"})
    await p.send("discord", "", "x", {})
    await p.send("discord", "888", "y", {})
    assert [r.url.path for r in seen] == ["/api/v10/channels/777/messages", "/api/v10/channels/888/messages"]


async def test_missing_secret_and_http_errors_fail_cleanly() -> None:
    p, _ = make(secrets={})
    receipt = await p.send("discord", "webhook", "x", {})
    assert receipt["ok"] is False and "discord/webhook_url" in receipt["detail"]

    def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Missing Access"})

    q = load().Plugin(FakeHost(SECRETS), transport=httpx.MockTransport(denied))
    q.initialize({}, [])
    receipt = await q.send("discord", "123", "x", {})
    assert receipt["ok"] is False and "403" in receipt["detail"]
    bad = await q.send("discord", "nonsense", "x", {})
    assert bad["ok"] is False and "unknown target" in bad["detail"]
