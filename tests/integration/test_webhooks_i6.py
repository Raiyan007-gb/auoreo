"""I6 webhook providers: the `WebhookProvider` interface, `telegram-callback`, plugin-provided
kinds (registry + JSON-RPC proxy), and the `allowed_cidrs: [github]` `/meta` preset with its
on-disk cache, offline fallback and doctor staleness row (spec §15, §19.11, §24.8)."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from pytest_httpx import HTTPXMock

from auoreo.api.app import create_app
from auoreo.automation import github_meta
from auoreo.automation import webhook_providers as providers
from auoreo.automation.webhook_providers import Verdict
from auoreo.automation.webhook_verify import signed_headers
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.harness.manifests import WebhookManifest
from auoreo.plugins.webhook_bridge import PluginWebhookProvider, register_providers, unregister_providers
from tests.conftest import write

TG_SECRET = "tg_secret-token_0123456789"
ROUTES = {
    "tg": 'provider: telegram-callback\nsecret: "secret:webhook/tg"\ndescription: telegram\n'
    'events: [message]\nprompt: "tg {{event.message.text}}"\n',
    "custom": 'provider: acme\nsecret: "secret:webhook/acme"\ndescription: plugin kind\n'
    'prompt: "acme {{event.n}}"\n',
    "gh": 'provider: github\nsecret: "secret:webhook/gh"\ndescription: github preset\n'
    'allowed_cidrs: [github]\nprompt: "gh"\n',
}


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=5, automation_start_delay_s=0)


@pytest.fixture
async def services(settings: Settings, workdir: Path, gateway: FakeGateway) -> AsyncIterator[Services]:
    for name, text in ROUTES.items():
        write(workdir / ".auoreo" / "webhooks" / f"{name}.yaml", f"name: {name}\n{text}")
    svc = await Services.create(settings, cwd=workdir, gateway=gateway, with_mcp=False, with_memory=False)
    svc.secrets.set("webhook/tg", TG_SECRET)
    svc.secrets.set("webhook/acme", "acme-secret")
    svc.secrets.set("webhook/gh", "gh-secret")
    try:
        yield svc
    finally:
        await svc.close()


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


def tg_update(uid: int, text: str = "hi") -> dict[str, Any]:
    return {"update_id": uid, "message": {"message_id": 1, "chat": {"id": 7}, "text": text}}


async def test_telegram_callback_secret_token_event_and_update_id(client: httpx.AsyncClient) -> None:
    body = json.dumps(tg_update(100)).encode()
    hdrs = signed_headers("telegram-callback", TG_SECRET, body, event="", delivery_id="")
    r = await client.post("/hooks/tg", content=body, headers=hdrs)
    assert r.status_code == 202, r.text
    assert r.json()["event"] == "message"
    dup = await client.post("/hooks/tg", content=body, headers=hdrs)  # same update_id
    assert dup.status_code == 200 and dup.json()["status"] == "duplicate"
    bad = {**hdrs, "X-Telegram-Bot-Api-Secret-Token": "wrong"}
    assert (await client.post("/hooks/tg", content=body, headers=bad)).status_code == 401
    missing = {"Content-Type": "application/json"}
    assert (await client.post("/hooks/tg", content=body, headers=missing)).status_code == 401
    cb = json.dumps({"update_id": 101, "callback_query": {"id": "q"}}).encode()
    r = await client.post("/hooks/tg", content=cb, headers=hdrs)  # event filter: message only
    assert r.status_code == 200 and r.json()["status"] == "ignored"


class AcmeProvider:
    kind = "acme"

    def __init__(self) -> None:
        self.parsed = 0

    async def verify(self, secret: str, headers: Mapping[str, str], body: bytes, now: float) -> Verdict:
        ok = headers.get("x-acme-key") == secret
        return Verdict(ok, "" if ok else "bad acme key", event="ping", delivery_id=headers.get("x-acme-id"))

    def event_name(self, headers: Mapping[str, str], payload: dict[str, Any]) -> str:
        return "unused"

    def delivery_id(self, headers: Mapping[str, str], body: bytes, payload: dict[str, Any]) -> str | None:
        return None

    async def parse(self, payload: dict[str, Any], headers: Mapping[str, str]) -> dict[str, Any]:
        self.parsed += 1
        return {**payload, "n": payload.get("n", 0) * 10}


async def test_plugin_kind_routes_through_the_registry(client: httpx.AsyncClient) -> None:
    body = json.dumps({"n": 4}).encode()
    hdrs = {"Content-Type": "application/json", "X-Acme-Key": "acme-secret", "X-Acme-Id": "a-1"}
    r = await client.post("/hooks/custom", content=body, headers=hdrs)
    assert r.status_code == 503  # no provider for `acme` yet: fail closed, never 500
    acme = AcmeProvider()
    assert providers.register(acme, "acme-plugin") is None
    try:
        assert providers.register(AcmeProvider(), "other") is not None  # owned by acme-plugin
        r = await client.post("/hooks/custom", content=body, headers=hdrs)
        assert r.status_code == 202 and r.json()["event"] == "ping" and acme.parsed == 1
        r = await client.post("/hooks/custom", content=body, headers={**hdrs, "X-Acme-Key": "nope"})
        assert r.status_code == 401 and r.json()["detail"] == "bad acme key"
    finally:
        providers.unregister("acme", "acme-plugin")
    assert providers.get("acme") is None


def test_builtin_kinds_cannot_be_replaced() -> None:
    fake = SimpleNamespace(kind="github")
    assert "built in" in str(providers.register(fake, "evil"))  # type: ignore[arg-type]
    assert providers.kinds()["telegram-callback"] == "builtin"
    WebhookManifest.model_validate(
        {"name": "x", "description": "d", "secret": "s", "prompt": "p", "provider": "x-y"}
    )
    with pytest.raises(ValueError):
        WebhookManifest.model_validate(
            {"name": "x", "description": "d", "secret": "s", "prompt": "p", "provider": "BAD"}
        )


class FakePeer:
    alive = True

    def __init__(self, reply: Any) -> None:
        self.reply = reply
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def request(self, method: str, params: dict[str, Any], timeout: float = 30) -> Any:
        self.calls.append((method, params))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


async def test_plugin_proxy_forwards_verify_and_parse_over_rpc() -> None:
    from auoreo.plugins.protocol import RpcError

    peer = FakePeer({"ok": True, "event": "push", "delivery_id": "d-9", "payload": {"x": 1}})
    st = SimpleNamespace(
        name="acme", peer=peer, trust={}, manifest=SimpleNamespace(provides=["webhook_provider"])
    )
    proxy = PluginWebhookProvider(st, "acme2")  # type: ignore[arg-type]
    v = await proxy.verify("sec", {"h": "1"}, b"{}", 1.0)
    assert v.ok and v.event == "push" and v.delivery_id == "d-9"
    method, params = peer.calls[-1]
    assert method == "webhook.verify" and params["kind"] == "acme2" and params["secret"] == "sec"
    assert params["body_b64"] == "e30="
    assert await proxy.parse({"x": 0}, {}) == {"x": 1}
    peer.reply = RpcError("boom")
    assert not (await proxy.verify("sec", {}, b"", 1.0)).ok
    assert await proxy.parse({"x": 0}, {}) == {"x": 0}  # optional: unchanged on error

    assert register_providers(
        SimpleNamespace(), st, {"webhook_providers": ["acme2", "BAD KIND", "github"]}
    ) == [  # type: ignore[arg-type]
        "acme2"
    ]
    assert providers.get("acme2") is not None
    unregister_providers(st)  # type: ignore[arg-type]
    assert providers.get("acme2") is None
    st.manifest.provides = ["tools"]  # advertising without `provides: [webhook_provider]` → ignored
    assert register_providers(SimpleNamespace(), st, {"webhook_providers": ["acme2"]}) == []  # type: ignore[arg-type]


async def test_github_preset_fetch_cache_offline_and_doctor(
    settings: Settings, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=settings.i6.webhooks.github_meta_url, json={"hooks": ["192.30.252.0/22", "bad"]}
    )
    assert await github_meta.expand(["10.0.0.0/8", "github"], settings) == ["10.0.0.0/8", "192.30.252.0/22"]
    fetched_at, hooks = github_meta.read_cache(settings.home) or (0, [])
    assert hooks == ["192.30.252.0/22"] and time.time() - fetched_at < 60
    assert github_meta.doctor_row(settings, ["gh"])[1] == "ok"  # type: ignore[index]
    assert github_meta.doctor_row(settings, []) is None
    # 8 days old + GitHub unreachable: stale ranges are still served, doctor warns.
    path = github_meta.cache_path(settings.home)
    path.write_text(json.dumps({"fetched_at": time.time() - 8 * 86_400, "hooks": hooks}))
    httpx_mock.add_exception(httpx.ConnectError("offline"), url=settings.i6.webhooks.github_meta_url)
    assert await github_meta.hook_cidrs(settings) == hooks
    for task in list(github_meta._refreshing.values()):
        await task  # the background refresh failed quietly
    row = github_meta.doctor_row(settings, ["gh"])
    assert row is not None and row[1] == "warn" and "8 days" in row[2]
    assert github_meta.read_cache(settings.home) == (pytest.approx(time.time() - 8 * 86_400, abs=60), hooks)


async def test_github_preset_without_cache_offline_fails_closed(
    settings: Settings, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_exception(httpx.ConnectError("offline"), url=settings.i6.webhooks.github_meta_url)
    assert await github_meta.expand(["github"], settings) == []
    assert github_meta.doctor_row(settings, ["gh"])[1] == "warn"  # type: ignore[index]


async def test_github_preset_on_a_route(client: httpx.AsyncClient, settings: Settings) -> None:
    body = json.dumps({"zen": "x"}).encode()
    hdrs = signed_headers("github", "gh-secret", body, event="ping", delivery_id="g-1")
    cache = github_meta.cache_path(settings.home)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"fetched_at": time.time(), "hooks": ["192.30.252.0/22"]}))
    r = await client.post("/hooks/gh", content=body, headers=hdrs)
    assert r.status_code == 403  # the test client (127.0.0.1) is not a GitHub hook address
    cache.write_text(json.dumps({"fetched_at": time.time(), "hooks": ["127.0.0.0/8"]}))
    r = await client.post("/hooks/gh", content=body, headers=hdrs)
    assert r.status_code == 202, r.text
