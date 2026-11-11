"""M7 Wave 2: consent-gated posting (`plugins/consent.py`, the host gate, reply mode)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.core.db import AuditRow, SuggestionRow
from auoreo.core.services import Services
from auoreo.core.settings import Settings, write_settings_value
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.plugins import consent
from auoreo.plugins.bridge import PluginDelivery
from auoreo.plugins.connectors import InboundRouter
from auoreo.plugins.host import PluginHost, PluginState

ON = {"strict_origins": True, "post_consent": True, "post_daily_cap": 2}
ANA = "ana@example.social"


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=5, m7={"agents": ON})


class Peer:
    alive = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, params: dict[str, Any], timeout: float = 30) -> Any:
        self.calls.append({"method": method, **params})
        return {"ok": True, "detail": "posted", "external_id": f"st{len(self.calls)}"}


def install(services: Services, name: str = "mastodon") -> tuple[PluginHost, PluginState, Peer]:
    """A running plugin state for the bundled manifest, with a recording peer."""
    host = PluginHost(services)
    loaded = services.harness.plugins[name]
    st = PluginState(name, loaded.manifest, loaded.path.parent, status="running")
    st.peer, st.deliveries = Peer(), [name]  # type: ignore[assignment]
    host.plugins[name] = st
    host.deliveries[name] = PluginDelivery(name, name, host._sender(st))
    services.ext["host"] = host
    return host, st, st.peer  # type: ignore[return-value]


async def add_post(services: Services, status: str = "accepted", **action: Any) -> int:
    act = {"type": "post", "plugin": "mastodon", "target": ANA, "text": "Thanks!", **action}
    async with services.db.session() as s:
        row = SuggestionRow(kind="post", title="Reply", body=act["text"], status=status, action=act)
        s.add(row)
        await s.commit()
        return row.id


async def audit_rows(services: Services, action: str) -> list[AuditRow]:
    async with services.db.session() as s:
        q = select(AuditRow).where(AuditRow.action == action).order_by(AuditRow.seq)
        return list((await s.execute(q)).scalars())


def test_bundled_mastodon_declares_consent_and_proxy(services: Services) -> None:
    m = services.harness.plugins["mastodon"].manifest
    assert {"consent:post", "state", "net:proxy", "secrets:mastodon/access_token"} <= set(m.consumes)
    assert not m.enabled and consent.reply_mode(services, "mastodon")
    assert not consent.reply_mode(services, "telegram")


async def test_send_without_consent_is_refused_even_with_a_forged_id(services: Services) -> None:
    host, st, peer = install(services)
    send = host._sender(st)
    for meta in ({}, {"consent_id": "1"}, {"consent_id": "999", "reply_to": {"status_id": "5"}}):
        res = await send("mastodon", ANA, "spam", dict(meta))
        assert not res["ok"] and "accepted suggestion" in res["detail"]
    assert peer.calls == []
    rows = await audit_rows(services, "plugin.post")
    assert [r.decision for r in rows] == ["deny"] * 3
    # a delivery through the shared registry (e.g. suggestions_home = mastodon) is refused too
    receipt = await host.deliveries["mastodon"].send(ANA, "hi", meta={"consent_id": "2"})
    assert not receipt.ok and peer.calls == []


async def test_post_sends_once_with_the_real_consent_id(services: Services) -> None:
    _, _, peer = install(services)
    sid = await add_post(services, consent_id="999", reply_to={"status_id": "96"})
    out = await consent.post(services, sid)
    assert out == {"ok": True, "detail": "posted", "external_id": "st1"}
    call = peer.calls[0]
    assert call["method"] == "delivery.send" and call["text"] == "Thanks!" and call["target"] == ANA
    assert call["meta"]["consent_id"] == str(sid)  # never the id carried in the action
    assert call["meta"]["reply_to"] == {"status_id": "96"}
    async with services.db.session() as s:
        row = await s.get(SuggestionRow, sid)
    assert row is not None and row.action["sent"] == "ok" and row.action["external_id"] == "st1"
    with pytest.raises(consent.ConsentError, match="already sent"):
        await consent.post(services, sid)
    assert len(peer.calls) == 1
    allow = [r for r in await audit_rows(services, "plugin.post") if r.decision == "allow"]
    assert allow and "Thanks!" not in str(services.audit.plain_detail(allow[0]))


@pytest.mark.parametrize(
    ("status", "action", "why"),
    [
        ("pending", {}, "not accepted"),
        ("denied", {}, "not accepted"),
        ("accepted", {"type": "prompt"}, "not a post"),
        ("accepted", {"plugin": "telegram"}, "not running"),
        ("accepted", {"plugin": "nope"}, "not running"),
        ("accepted", {"text": "  "}, "empty"),
    ],
)
async def test_post_refusals(services: Services, status: str, action: dict[str, Any], why: str) -> None:
    _, _, peer = install(services)
    sid = await add_post(services, status=status, **action)
    with pytest.raises(consent.ConsentError, match=why):
        await consent.post(services, sid)
    assert peer.calls == []


async def test_post_to_a_plugin_without_consent_is_refused(services: Services) -> None:
    host, _, _ = install(services, "telegram")  # running, but it does not consume consent:post
    peer = host.plugins["telegram"].peer
    sid = await add_post(services, plugin="telegram")
    with pytest.raises(consent.ConsentError, match="consent:post"):
        await consent.post(services, sid)
    with pytest.raises(consent.ConsentError, match="not found"):
        await consent.post(services, 424242)
    assert peer is not None and peer.calls == []  # type: ignore[attr-defined]


async def test_daily_cap_fails_closed(services: Services) -> None:
    _, _, peer = install(services)
    ids = [await add_post(services) for _ in range(3)]
    assert (await consent.post(services, ids[0]))["ok"] and (await consent.post(services, ids[1]))["ok"]
    with pytest.raises(consent.ConsentError, match="daily post cap"):
        await consent.post(services, ids[2])
    assert len(peer.calls) == 2


async def test_flag_off_refuses_post(home: Path, workdir: Path) -> None:
    st = Settings(home=home, gateway="fake")
    svc = await Services.create(st, cwd=workdir, gateway=FakeGateway(), with_mcp=False)
    try:
        install(svc)
        sid = await add_post(svc)
        with pytest.raises(consent.ConsentError, match="post_consent"):
            await consent.post(svc, sid)
    finally:
        await svc.close()


class Chat:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def __call__(self, chat_id: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
        self.sent.append((chat_id, text))
        return {"ok": True}


async def test_reply_mode_proposes_instead_of_sending(services: Services, gateway: FakeGateway) -> None:
    write_settings_value(services.settings.home, "plugins.mastodon.allowlist", [ANA])
    gateway.script.append(Scripted(text="Here is my draft."))
    router, chat = InboundRouter(services, "mastodon", timeout_s=20), Chat()
    params = {"chat_id": ANA, "text": "@bot hi", "reply_to": {"status_id": "96"}}
    out = await router.handle(params, chat)
    assert chat.sent == [] and out["ok"] and out["proposed"]
    async with services.db.session() as s:
        row = await s.get(SuggestionRow, out["proposed"])
    assert row is not None and row.kind == "post" and row.status == "pending"
    assert row.body == row.action["text"] == "Here is my draft."
    assert row.action == {
        "type": "post",
        "plugin": "mastodon",
        "target": ANA,
        "text": "Here is my draft.",
        "reply_to": {"status_id": "96"},
    }
    assert row.dedupe_key == "post:mastodon:96" and row.deliver == "home"
    # a public "/approve all" is not a chat command on a consent connector
    cmd = await router.handle({"chat_id": ANA, "text": "/approve all", "reply_to": {"status_id": "97"}}, chat)
    assert not cmd.get("command") and chat.sent == []


async def test_reply_mode_with_flag_off_proposes_nothing(
    home: Path, workdir: Path, gateway: FakeGateway
) -> None:
    st = Settings(home=home, gateway="fake")
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        write_settings_value(home, "plugins.mastodon.allowlist", [ANA])
        chat = Chat()
        out = await InboundRouter(svc, "mastodon", timeout_s=20).handle(
            {"chat_id": ANA, "text": "hi", "reply_to": {"status_id": "1"}}, chat
        )
        assert chat.sent == [] and not out["ok"] and out["proposed"] is None
        async with svc.db.session() as s:
            assert (await s.execute(select(SuggestionRow))).first() is None
    finally:
        await svc.close()


async def test_state_requests_need_consumes_and_are_capped(services: Services) -> None:
    from auoreo.plugins.protocol import RpcError
    from auoreo.plugins.requests import HOST_REQUESTS

    host, st, _ = install(services)
    _, tg, _ = install(services, "telegram")
    with pytest.raises(RpcError, match="consumes"):
        await HOST_REQUESTS["state.get"](host, tg, {})
    assert await HOST_REQUESTS["state.get"](host, st, {}) == {"state": {}}
    await HOST_REQUESTS["state.set"](host, st, {"state": {"cursor": "6", "reads": 2}})
    assert await HOST_REQUESTS["state.get"](host, st, {}) == {"state": {"cursor": "6", "reads": 2}}
    with pytest.raises(RpcError, match="16384"):
        await HOST_REQUESTS["state.set"](host, st, {"state": {"x": "a" * 20000}})
    with pytest.raises(RpcError, match="object"):
        await HOST_REQUESTS["state.set"](host, st, {"state": [1]})


async def test_proxy_token_is_minted_per_start_and_revoked(services: Services) -> None:
    from auoreo.plugins import requests_m7
    from auoreo.tools.egress import proxy_of

    _, st, _ = install(services)
    _, tg, _ = install(services, "telegram")
    assert await requests_m7.init_extra(services, tg) == {}  # no `net:proxy`: nothing minted
    extra = await requests_m7.init_extra(services, st)
    proxy = proxy_of(services)
    assert proxy is not None and extra["proxy"].startswith("http://auoreo:")
    token = extra["proxy"].split(":")[2].split("@")[0]
    entry = proxy.peek(token)
    assert entry is not None and entry.label == "plugin:mastodon"
    again = await requests_m7.init_extra(services, st)  # a restart revokes the old token
    assert proxy.peek(token) is None and again != extra
    requests_m7.release("mastodon")
    assert proxy.status()["tokens"] == 0
