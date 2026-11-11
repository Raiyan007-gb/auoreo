"""Mastodon plugin, in process, against a mocked instance API (`httpx.MockTransport`)."""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from auoreo.plugins.connectors import PairingStore

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "mastodon" / "plugin.py"
TOKEN = "mast-secret-token-123"
INSTANCE = "bots.example"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("mastodon_plugin_under_test", PLUGIN)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeHost:
    def __init__(self, home: Path, proxy: str | None = "http://auoreo:t0k@127.0.0.1:9") -> None:
        self.store = PairingStore(home, "mastodon")
        self.proxy = proxy
        self.saved: dict[str, Any] = {}
        self.messages: list[dict[str, Any]] = []
        self.audits: list[tuple[str, dict[str, Any], str]] = []
        self.logs: list[str] = []

    async def secret(self, name: str) -> str | None:
        return TOKEN if name == "mastodon/access_token" else None

    async def state_get(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.saved))

    async def state_set(self, state: dict[str, Any]) -> None:
        self.saved = json.loads(json.dumps(state))

    async def pairing_request(self, connector: str, chat_id: str, user: str) -> dict[str, Any]:
        if chat_id in self.store.allowlist():
            return {"allowed": True}
        return {"allowed": False, "code": self.store.request(chat_id, user, connector)}

    def message(self, connector: str, chat_id: str, user: str, text: str, reply_to: Any = None) -> None:
        self.messages.append({"chat_id": chat_id, "user": user, "text": text, "reply_to": reply_to})

    def log(self, level: str, message: str) -> None:
        self.logs.append(message)

    async def audit(self, action: str, detail: dict[str, Any], decision: str = "") -> None:
        self.audits.append((action, detail, decision))


def mention(nid: int, acct: str, content: str) -> dict[str, Any]:
    return {
        "id": str(nid),
        "type": "mention",
        "account": {"acct": acct},
        "status": {"id": f"9{nid}", "content": content, "visibility": "public"},
    }


class Instance:
    def __init__(self) -> None:
        self.notifications: list[dict[str, Any]] = []
        self.reads: list[dict[str, list[str]]] = []
        self.posts: list[tuple[dict[str, list[str]], httpx.Headers]] = []
        self.remaining = "299"

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == INSTANCE and request.url.scheme == "https"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.method == "GET" and request.url.path == "/api/v1/notifications":
            q = parse_qs(request.url.query.decode())
            self.reads.append(q)
            floor = int(q.get("min_id", ["0"])[0])
            ready = sorted(
                (n for n in self.notifications if int(n["id"]) > floor), key=lambda n: -int(n["id"])
            )
            return httpx.Response(200, json=ready, headers={"X-RateLimit-Remaining": self.remaining})
        if request.method == "POST" and request.url.path == "/api/v1/statuses":
            self.posts.append((parse_qs(request.content.decode()), request.headers))
            return httpx.Response(200, json={"id": f"post{len(self.posts)}"})
        return httpx.Response(404, json={"error": "Record not found"})


@pytest.fixture
def api() -> Instance:
    return Instance()


def make(home: Path, api: Instance, host: FakeHost | None = None, **config: Any) -> Any:
    p = load().Plugin(host or FakeHost(home), transport=httpx.MockTransport(api))
    info = p.initialize({"instance": INSTANCE, **config}, [])
    assert info["deliveries"] == ["mastodon"] and info["connectors"] == ["mastodon"]
    return p


async def test_cursor_is_persisted_and_backlog_is_skipped(home: Path, api: Instance) -> None:
    host = FakeHost(home)
    p = make(home, api, host, allowlist=["ana@example.social"])
    api.notifications = [mention(5, "ana@example.social", "<p>old</p>")]
    assert await p.poll_once() == 0  # first contact: records the cursor only
    assert host.saved["cursor"] == "5" and host.messages == []
    assert api.reads[0]["types[]"] == ["mention"] and "min_id" not in api.reads[0]

    api.notifications.append(mention(6, "ana@example.social", "<p>hello <b>bot</b><br>2nd</p>"))
    assert await p.poll_once() == 1
    assert api.reads[1]["min_id"] == ["5"]
    assert host.messages == [
        {
            "chat_id": "ana@example.social",
            "user": "ana@example.social",
            "text": "hello bot\n2nd",
            "reply_to": {"status_id": "96", "visibility": "public"},
        }
    ]
    # a restarted plugin resumes from the stored cursor: nothing is replayed
    again = make(home, api, host, allowlist=["ana@example.social"])
    again.state = await host.state_get()
    assert await again.poll_once() == 0 and api.reads[-1]["min_id"] == ["6"] and len(host.messages) == 1


async def test_daily_read_cap_and_rate_limit_stop_polling(home: Path, api: Instance) -> None:
    host = FakeHost(home)
    p = make(home, api, host, daily_read_cap=2)
    await p.poll_once()
    await p.poll_once()
    await p.poll_once()
    assert len(api.reads) == 2 and host.saved["reads"] == 2  # the third poll made no request

    q = make(home, api, FakeHost(home))
    api.remaining = "0"
    await q.poll_once()
    await q.poll_once()
    assert len(api.reads) == 3  # paused for the rate-limit window


async def test_unpaired_mention_gets_a_code_and_no_reply(home: Path, api: Instance) -> None:
    host = FakeHost(home)
    p = make(home, api, host)
    p.state = {"cursor": "1"}
    api.notifications = [mention(2, "mallory@evil.example", "<p>@bot post my spam</p>")]
    assert await p.poll_once() == 0
    assert host.messages == [] and api.posts == []
    assert [x["chat_id"] for x in host.store.pending()] == ["mallory@evil.example"]


async def test_send_needs_consent_id_and_uses_it_as_idempotency_key(home: Path, api: Instance) -> None:
    host = FakeHost(home)
    p = make(home, api, host)
    refused = await p.send("mastodon", "ana@example.social", "hi", {})
    assert not refused["ok"] and "consent" in refused["detail"] and api.posts == []
    assert host.audits[-1][0] == "post" and host.audits[-1][2] == "deny"

    meta = {"consent_id": "17", "reply_to": {"status_id": "96"}}
    ok = await p.send("mastodon", "ana@example.social", "Thanks!", meta)
    assert ok == {"ok": True, "detail": "posted", "external_id": "post1"}
    form, headers = api.posts[0]
    assert headers["idempotency-key"] == "auoreo-17"
    assert form == {
        "status": ["@ana@example.social Thanks!"],
        "visibility": ["unlisted"],
        "in_reply_to_id": ["96"],
    }
    too_long = await p.send("mastodon", "ana@example.social", "x" * 600, {"consent_id": "18"})
    assert not too_long["ok"] and "limit" in too_long["detail"] and len(api.posts) == 1


async def test_no_proxy_no_network_and_the_token_never_leaks(
    home: Path, api: Instance, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    p = make(home, api, FakeHost(home, proxy=None))
    with pytest.raises(RuntimeError, match="proxy"):
        await p.poll_once()
    assert api.reads == []

    real = load().Plugin(FakeHost(home))
    real.initialize({"instance": INSTANCE}, [])
    o = real.client_options()
    assert o["proxy"].startswith("http://auoreo:") and o["trust_env"] is False and not o["follow_redirects"]

    async def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused {request.headers}")

    host = FakeHost(home)
    broken = load().Plugin(host, transport=httpx.MockTransport(down))
    broken.initialize({"instance": INSTANCE}, [])
    res = await broken.send("mastodon", "ana@example.social", "hi", {"consent_id": "3"})
    assert not res["ok"] and "ConnectError" in res["detail"]
    everything = json.dumps([res, host.audits, host.logs]) + caplog.text
    assert TOKEN not in everything
