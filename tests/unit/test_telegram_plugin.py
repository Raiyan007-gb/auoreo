"""Telegram plugin, in process, against a mocked Bot API (`httpx.MockTransport`)."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

from auoreo.core import settings_store
from auoreo.plugins.connectors import PairingStore

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "telegram" / "plugin.py"
TOKEN = "123:abc"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tg_plugin_under_test", PLUGIN)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeHost:
    """The plugin-side HostAPI, backed by the real host `PairingStore`."""

    def __init__(self, home: Path) -> None:
        self.store = PairingStore(home, "telegram")
        self.messages: list[dict[str, Any]] = []
        self.secrets_asked: list[str] = []

    async def secret(self, name: str) -> str | None:
        self.secrets_asked.append(name)
        return TOKEN if name == "telegram/bot_token" else None

    async def pairing_request(self, connector: str, chat_id: str, user: str) -> dict[str, Any]:
        if chat_id in self.store.allowlist():
            return {"allowed": True}
        return {"allowed": False, "code": self.store.request(chat_id, user, connector)}

    def message(self, connector: str, chat_id: str, user: str, text: str, reply_to: Any = None) -> None:
        self.messages.append({"chat_id": chat_id, "user": user, "text": text, "reply_to": reply_to})

    def log(self, level: str, message: str) -> None:
        pass


class BotApi:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.polls: list[dict[str, Any]] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path.startswith(f"/bot{TOKEN}/")
        method = request.url.path.rsplit("/", 1)[1]
        body = json.loads(request.content or b"{}")
        if method == "getUpdates":
            self.polls.append(body)
            ready = [u for u in self.updates if u["update_id"] >= body.get("offset", 0)]
            if not ready:
                await asyncio.sleep(0.02)  # stand-in for the long-poll wait
            return httpx.Response(200, json={"ok": True, "result": ready})
        if method == "sendMessage":
            self.sent.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 100 + len(self.sent)}})
        return httpx.Response(404, json={"ok": False, "description": "Not Found"})


def update(uid: int, chat: int, text: str, **msg: Any) -> dict[str, Any]:
    return {
        "update_id": uid,
        "message": {
            "message_id": uid * 10,
            "chat": {"id": chat, "type": "private"},
            "from": {"id": chat, "username": f"user{chat}"},
            "text": text,
            **msg,
        },
    }


@pytest.fixture
def api() -> BotApi:
    return BotApi()


@pytest.fixture
def plugin(api: BotApi, home: Path) -> Any:
    p = load().Plugin(FakeHost(home), transport=httpx.MockTransport(api))
    info = p.initialize({}, ["sessions", "secrets:telegram/bot_token"])
    assert info["deliveries"] == ["telegram"] and info["connectors"] == ["telegram"]
    return p


async def test_unknown_chat_gets_a_pairing_code_then_pairs(plugin: Any, api: BotApi, home: Path) -> None:
    api.updates = [update(1, 42, "hi there")]
    assert await plugin.poll_once() == 1
    assert plugin.offset == 2 and api.polls[0] == {"offset": 0, "timeout": 25, "allowed_updates": ["message"]}
    assert plugin.host.messages == []  # not forwarded before pairing
    assert len(api.sent) == 1 and api.sent[0]["chat_id"] == "42"
    code = api.sent[0]["text"].split("Pairing code: ")[1].split("\n")[0]
    assert len(code) == 6 and code.isdigit() and "parse_mode" not in api.sent[0]

    # the same chat asking again gets the same code
    api.updates.append(update(2, 42, "hello?"))
    await plugin.poll_once()
    assert code in api.sent[1]["text"]

    # the operator pairs (`auoreo plugin pair telegram <code>` / POST …/pair)
    assert plugin.host.store.pair(code) == "42"
    settings_json = settings_store.read(home)
    assert settings_json["plugins"]["telegram"]["allowlist"] == ["42"]
    assert plugin.host.store.pair(code) is None  # single use

    api.updates.append(update(3, 42, "now paired"))
    await plugin.poll_once()
    assert plugin.host.messages == [
        {"chat_id": "42", "user": "user42", "text": "now paired", "reply_to": {"message_id": 30}}
    ]
    assert plugin.offset == 4 and len(api.sent) == 2  # no new pairing message


async def test_allowlisted_chat_is_forwarded_with_topic_thread(plugin: Any, api: BotApi) -> None:
    plugin.config_update({"allowlist": ["-100"]})
    api.updates = [update(7, -100, "in a topic", is_topic_message=True, message_thread_id=55)]
    await plugin.poll_once()
    msg = plugin.host.messages[0]
    assert msg["reply_to"] == {"message_id": 70, "thread_id": 55}

    receipt = await plugin.send("telegram", "-100", "answer", {"reply_to": msg["reply_to"]})
    assert receipt == {"ok": True, "detail": "1 message(s)", "external_id": 101}
    assert api.sent[-1] == {"chat_id": "-100", "text": "answer", "message_thread_id": 55}


async def test_long_replies_are_chunked_at_4000(plugin: Any, api: BotApi) -> None:
    text = ("x" * 3000 + "\n") * 3  # 9003 chars with newlines to split on
    receipt = await plugin.send("telegram", "42", text, {})
    assert receipt["ok"] and receipt["detail"] == "3 message(s)"
    assert all(len(m["text"]) <= 4000 for m in api.sent)
    assert "".join(m["text"] for m in api.sent).replace("\n", "") == "x" * 9000
    mod = load()
    assert [len(c) for c in mod.chunk("y" * 8500)] == [4000, 4000, 500]
    assert mod.chunk("") == [""]


async def test_api_errors_become_failed_receipts(home: Path) -> None:
    async def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

    p = load().Plugin(FakeHost(home), transport=httpx.MockTransport(broken))
    p.initialize({}, [])
    receipt = await p.send("telegram", "1", "x", {})
    assert receipt["ok"] is False and "chat not found" in receipt["detail"]


async def test_connector_start_and_stop_poll_loop(plugin: Any, api: BotApi) -> None:
    plugin.config_update({"allowlist": ["5"]})
    api.updates = [update(1, 5, "ping")]
    await plugin.connector_start("telegram")
    for _ in range(100):
        if plugin.host.messages:
            break
        await asyncio.sleep(0.01)
    await plugin.shutdown()
    assert plugin.host.messages[0]["text"] == "ping" and plugin.host.secrets_asked == ["telegram/bot_token"]
