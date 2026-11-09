"""M7 W3: connector `routes` (rule → agent) evaluated in `InboundRouter.handle`.

Design: docs/designs/m7-agents-bots.md (CUJ 1, CUJ 4). Routing is on when a connector's config
has `routes`; without them every message goes to the connector's fixed agent as before.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import select

from auoreo.core.db import AuditRow
from auoreo.core.services import Services
from auoreo.core.settings import write_settings_value
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.harness.manifests import PluginManifest
from auoreo.plugins import routes
from auoreo.plugins.connectors import InboundRouter
from auoreo.plugins.host import ConfigError, validate_config

REPO = Path(__file__).resolve().parents[2]
CHAT, OTHER = "111", "222"
AGENTS = {"default", "ops", "researcher"}
OPS = {"match": {"field": "text", "regex": "^/ops\\b"}, "agent": "ops"}
ASK = {"match": {"field": "text", "regex": "\\?$"}, "agent": "researcher"}
PAYLOAD = {"text": "/ops disk usage", "user": "ana", "chat_id": CHAT, "name": "telegram"}


class Chat:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    async def __call__(self, chat_id: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
        self.sent.append((chat_id, text, meta))
        return {"ok": True, "detail": "sent"}


def configure(
    services: Services, rules: list[dict[str, Any]] | None, owners: list[str] | None = None
) -> None:
    home = services.settings.home
    write_settings_value(home, "plugins.telegram.allowlist", [CHAT, OTHER])
    if rules is not None:
        write_settings_value(home, "plugins.telegram.routes", rules)
    if owners is not None:
        write_settings_value(home, "plugins.telegram.owners", owners)


async def routed_rows(services: Services) -> list[tuple[str, str, dict[str, Any]]]:
    async with services.db.session() as s:
        q = select(AuditRow).where(AuditRow.action == "inbound.routed").order_by(AuditRow.seq)
        rows = list((await s.execute(q)).scalars())
    return [(r.reason or "", r.session_id or "", services.audit.plain_detail(r)) for r in rows]


def sessions_file(services: Services) -> dict[str, str]:
    path = services.settings.home / "plugins" / "telegram" / "sessions.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def main_requests(gateway: FakeGateway) -> list[Any]:
    return [r for r in gateway.requests if r.metadata.get("purpose") not in {"title", "compaction"}]


# ------------------------------------------------------------------ select
def test_first_matching_route_wins_and_no_match_is_the_default() -> None:
    both = [{**OPS, "match": {"field": "text", "contains": "disk"}, "agent": "researcher"}, OPS]
    assert routes.select(both, PAYLOAD, owner=True, agents=AGENTS) == ("researcher", "rule")
    assert routes.select([OPS, ASK], PAYLOAD, owner=True, agents=AGENTS) == ("ops", "rule")
    assert routes.select([OPS, ASK], {**PAYLOAD, "text": "thanks"}, owner=True, agents=AGENTS) == (
        None,
        "default",
    )
    assert routes.select([], PAYLOAD, owner=True, agents=AGENTS) == (None, "default")
    assert routes.select(None, PAYLOAD, owner=True, agents=AGENTS) == (None, "default")


def test_combinators_and_other_payload_fields() -> None:
    rule = {
        "match": {
            "all": [
                {"field": "name", "equals": "telegram"},
                {"any": [{"field": "text", "contains": "deploy"}, {"field": "text", "contains": "disk"}]},
                {"not": {"field": "user", "equals": "mallory"}},
            ]
        },
        "agent": "ops",
    }
    assert routes.select([rule], PAYLOAD, owner=True, agents=AGENTS) == ("ops", "rule")
    assert routes.select([rule], {**PAYLOAD, "user": "mallory"}, owner=True, agents=AGENTS)[0] is None
    assert routes.select([rule], {**PAYLOAD, "name": "discord"}, owner=True, agents=AGENTS)[0] is None


def test_regex_is_case_sensitive_search_on_the_first_1000_characters() -> None:
    assert routes.select([OPS], {**PAYLOAD, "text": "/OPS x"}, owner=True, agents=AGENTS)[0] is None
    late = routes.payload_of({"text": "x" * 1000 + "?", "chat_id": CHAT, "user": "ana", "name": "telegram"})
    assert len(late["text"]) == 1000 and late == {**late, "user": "ana", "chat_id": CHAT, "name": "telegram"}
    assert routes.select([ASK], late, owner=True, agents=AGENTS) == (None, "default")


def test_owner_restriction_fails_closed() -> None:
    assert routes.select([OPS], PAYLOAD, owner=False, agents=AGENTS) == (None, "owners_only")
    open_rule = {**OPS, "restrict": "chat"}
    assert routes.select([open_rule], PAYLOAD, owner=False, agents=AGENTS) == ("ops", "rule")
    # a later open route still applies after an owner-only one was skipped
    assert routes.select(
        [OPS, {**open_rule, "agent": "researcher"}], PAYLOAD, owner=False, agents=AGENTS
    ) == (
        "researcher",
        "rule",
    )
    assert routes.is_owner({"owners": [CHAT]}, CHAT) and routes.is_owner({"owners": [111]}, CHAT)
    for cfg in ({}, {"owners": []}, {"owners": None}, {"owners": [OTHER]}, {"owners": CHAT}):
        assert not routes.is_owner(cfg, CHAT), cfg  # unset or empty owners: no owner-only route matches


@pytest.mark.parametrize(
    "bad",
    [
        {"match": {"field": "text", "regex": "("}, "agent": "ops"},
        {"match": {"field": "text", "regex": "a" * 201}, "agent": "ops"},
        {"match": {"any": [{"field": "text", "regex": "[z-a]"}]}, "agent": "ops"},
        {"match": {"field": "text", "in_file": "names.txt"}, "agent": "ops"},
        {"match": {"not": {"field": "text", "in_file": "names.txt"}}, "agent": "ops"},
        {"match": {"field": "text"}, "agent": "ops", "restrict": "everyone"},
        {"match": {"field": "text", "equals": "x"}},
        {"match": {}, "agent": "ops"},
        {"agent": "ops"},
        "not a route",
    ],
)
def test_invalid_routes_are_refused_at_save_and_skipped_at_runtime(bad: Any) -> None:
    with pytest.raises(routes.RouteError, match="/routes/1"):
        routes.validate({"routes": [ASK, bad]}, AGENTS)
    assert routes.select([bad, OPS], PAYLOAD, owner=True, agents=AGENTS) == ("ops", "rule")
    assert routes.select([bad], PAYLOAD, owner=True, agents=AGENTS) == (None, "invalid_route")


def test_unknown_agent_is_refused_at_save_and_skipped_at_runtime() -> None:
    ghost = {**OPS, "agent": "ghost"}
    with pytest.raises(routes.RouteError, match=r"/routes/0/agent.*ghost"):
        routes.validate({"routes": [ghost]}, AGENTS)
    routes.validate({"routes": [ghost]}, None)  # no registry at hand (offline listing): shape only
    assert routes.select([ghost, OPS], PAYLOAD, owner=True, agents=AGENTS) == ("ops", "rule")
    assert routes.select([ghost], PAYLOAD, owner=True, agents=AGENTS) == (None, "unknown_agent")
    # an unknown agent on a route that does not match is not the reason for the default
    assert routes.select([ghost], {**PAYLOAD, "text": "hi"}, owner=True, agents=AGENTS) == (None, "default")


def test_validate_accepts_good_routes_and_non_list_is_refused() -> None:
    routes.validate({"routes": [OPS, {**ASK, "restrict": "chat"}]}, AGENTS)
    routes.validate({}, AGENTS)
    with pytest.raises(routes.RouteError, match="/routes"):
        routes.validate({"routes": {"match": {}}}, AGENTS)
    assert routes.select({"match": {}}, PAYLOAD, owner=True, agents=AGENTS) == (None, "invalid_route")


def _manifest(name: str) -> PluginManifest:
    return PluginManifest.model_validate(
        yaml.safe_load((REPO / "plugins" / name / "plugin.yaml").read_text())
    )


def test_plugin_config_validation_names_the_bad_route() -> None:
    telegram = _manifest("telegram")
    validate_config(telegram, {"routes": [OPS, {**ASK, "restrict": "chat"}]}, AGENTS)
    validate_config(telegram, {"agent": "ops"}, AGENTS)
    with pytest.raises(ConfigError, match=r"plugins\.telegram invalid at /routes/1/agent"):
        validate_config(telegram, {"routes": [OPS, {**ASK, "agent": "ghost"}]}, AGENTS)
    with pytest.raises(ConfigError, match="/routes/0"):
        validate_config(telegram, {"routes": [{"match": {"field": "text", "regex": "("}, "agent": "ops"}]})
    with pytest.raises(ConfigError, match="/routes/0"):
        validate_config(telegram, {"routes": [{**OPS, "surprise": 1}]}, AGENTS)
    with pytest.raises(ConfigError):  # connectors that did not opt in keep rejecting the key
        validate_config(_manifest("discord"), {"routes": [OPS]}, AGENTS)


# ------------------------------------------------------------- InboundRouter
async def test_one_chat_several_specialists(services: Services, gateway: FakeGateway) -> None:
    configure(services, [OPS, ASK], owners=[CHAT])
    router, chat = InboundRouter(services, "telegram", timeout_s=20), Chat()
    gateway.script.extend([Scripted(text="disk is fine"), Scripted(text="a lot"), Scripted(text="welcome")])
    first = await router.handle({"chat_id": CHAT, "text": "/ops disk usage on lab", "user": "ana"}, chat)
    second = await router.handle({"chat_id": CHAT, "text": "what changed in 1.106?", "user": "ana"}, chat)
    third = await router.handle({"chat_id": CHAT, "text": "thanks", "user": "ana"}, chat)
    sids = [first["session_id"], second["session_id"], third["session_id"]]
    assert len(set(sids)) == 3 and all(o["ok"] for o in (first, second, third))
    agents = [(await services.get_session(s)).agent for s in sids]  # type: ignore[union-attr]
    assert agents == ["ops", "researcher", "default"]
    assert sessions_file(services) == {f"{CHAT}:ops": sids[0], f"{CHAT}:researcher": sids[1], CHAT: sids[2]}
    # a non-default agent labels its reply; the default agent's reply is unchanged
    assert [t for _, t, _ in chat.sent] == ["[ops] disk is fine", "[researcher] a lot", "welcome"]
    sent = main_requests(gateway)
    assert "/ops disk usage on lab" in str(sent[0].messages[-1])  # the text reaches the agent unchanged
    rows = await routed_rows(services)
    assert [(why, sid) for why, sid, _ in rows] == [
        ("rule", sids[0]),
        ("rule", sids[1]),
        ("default", sids[2]),
    ]
    assert rows[0][2] == {"plugin": "telegram", "chat_id": CHAT, "agent": "ops"}
    assert rows[2][2] == {"plugin": "telegram", "chat_id": CHAT, "agent": "default"}
    # a follow-up reuses the routed session
    gateway.script.append(Scripted(text="still fine"))
    again = await router.handle({"chat_id": CHAT, "text": "/ops and now?", "user": "ana"}, chat)
    assert again["session_id"] == sids[0] and len(sessions_file(services)) == 3


async def test_no_routes_is_todays_behaviour(services: Services, gateway: FakeGateway) -> None:
    configure(services, None)
    router, chat = InboundRouter(services, "telegram", timeout_s=20), Chat()
    gateway.script.extend([Scripted(text="one"), Scripted(text="two")])
    a = await router.handle({"chat_id": CHAT, "text": "/ops disk?", "user": "ana"}, chat)
    b = await router.handle({"chat_id": CHAT, "text": "hello?", "user": "ana"}, chat, "researcher")
    assert a["session_id"] == b["session_id"]  # one session per chat, bound at first contact
    assert (await services.get_session(a["session_id"])).agent == "default"  # type: ignore[union-attr]
    assert sessions_file(services) == {CHAT: a["session_id"]}
    assert [t for _, t, _ in chat.sent] == ["one", "two"]
    assert set(chat.sent[0][2]) == {"reply_to", "connector", "session_id"}
    assert await routed_rows(services) == []


async def test_fixed_agent_is_the_fallback_and_is_not_labelled(
    services: Services, gateway: FakeGateway
) -> None:
    configure(services, [OPS, {**ASK, "restrict": "chat"}], owners=[CHAT])
    router, chat = InboundRouter(services, "telegram", timeout_s=20), Chat()
    gateway.script.extend([Scripted(text="plain"), Scripted(text="also plain")])
    a = await router.handle({"chat_id": CHAT, "text": "hello", "user": "ana"}, chat, "researcher")
    b = await router.handle({"chat_id": CHAT, "text": "really?", "user": "ana"}, chat, "researcher")
    assert a["session_id"] == b["session_id"]  # a rule naming the connector's own agent: same session
    assert (await services.get_session(a["session_id"])).agent == "researcher"  # type: ignore[union-attr]
    assert sessions_file(services) == {CHAT: a["session_id"]}
    assert [t for _, t, _ in chat.sent] == ["plain", "also plain"]
    assert [(w, d["agent"]) for w, _, d in await routed_rows(services)] == [
        ("default", "researcher"),
        ("rule", "researcher"),
    ]


async def test_owner_only_routes_need_an_owner_chat(services: Services, gateway: FakeGateway) -> None:
    configure(services, [OPS, {**ASK, "restrict": "chat"}], owners=[CHAT])
    router, chat = InboundRouter(services, "telegram", timeout_s=20), Chat()
    gateway.script.extend([Scripted(text="no ops for you"), Scripted(text="researched")])
    denied = await router.handle({"chat_id": OTHER, "text": "/ops rm -rf", "user": "eve"}, chat)
    opened = await router.handle({"chat_id": OTHER, "text": "why?", "user": "eve"}, chat)
    assert (await services.get_session(denied["session_id"])).agent == "default"  # type: ignore[union-attr]
    assert (await services.get_session(opened["session_id"])).agent == "researcher"  # type: ignore[union-attr]
    assert [w for w, _, _ in await routed_rows(services)] == ["owners_only", "rule"]
    assert chat.sent[0][1] == "no ops for you" and chat.sent[1][1] == "[researcher] researched"


async def test_unset_owners_never_matches_an_owner_route(services: Services, gateway: FakeGateway) -> None:
    configure(services, [OPS])
    router, chat = InboundRouter(services, "telegram", timeout_s=20), Chat()
    gateway.script.append(Scripted(text="default answered"))
    out = await router.handle({"chat_id": CHAT, "text": "/ops disk", "user": "ana"}, chat)
    assert (await services.get_session(out["session_id"])).agent == "default"  # type: ignore[union-attr]
    assert [w for w, _, _ in await routed_rows(services)] == ["owners_only"]


async def test_bad_routes_on_disk_do_not_strand_the_chat(services: Services, gateway: FakeGateway) -> None:
    broken = {"match": {"field": "text", "regex": "("}, "agent": "ops"}
    configure(services, [broken, {**OPS, "agent": "ghost"}], owners=[CHAT])
    router, chat = InboundRouter(services, "telegram", timeout_s=20), Chat()
    gateway.script.extend([Scripted(text="fallback one"), Scripted(text="fallback two")])
    out = await router.handle({"chat_id": CHAT, "text": "/ops disk", "user": "ana"}, chat)
    assert out["ok"] and chat.sent[-1][1] == "fallback one"
    assert (await services.get_session(out["session_id"])).agent == "default"  # type: ignore[union-attr]
    write_settings_value(services.settings.home, "plugins.telegram.routes", [{**OPS, "agent": "ghost"}])
    await router.handle({"chat_id": CHAT, "text": "/ops disk", "user": "ana"}, chat)
    assert [w for w, _, _ in await routed_rows(services)] == ["invalid_route", "unknown_agent"]


async def test_approval_commands_bypass_routing(services: Services) -> None:
    everything = {"match": {"field": "text", "regex": "."}, "agent": "ops", "restrict": "chat"}
    configure(services, [everything])
    router, chat = InboundRouter(services, "telegram", timeout_s=20), Chat()
    out = await router.handle({"chat_id": CHAT, "text": "/pending"}, chat)
    assert out["command"] and chat.sent[-1][1] == "No approvals are pending."
    assert await services.list_sessions() == [] and await routed_rows(services) == []


async def test_concurrent_first_messages_make_one_session(services: Services, gateway: FakeGateway) -> None:
    configure(services, [OPS], owners=[CHAT])
    chat = Chat()
    gateway.script.extend([Scripted(text="a"), Scripted(text="b"), Scripted(text="c")])
    # the host builds one router per message, so the lock cannot live on the router
    outs = await asyncio.gather(
        *(
            InboundRouter(services, "telegram", timeout_s=20).handle(
                {"chat_id": CHAT, "text": f"/ops {i}", "user": "ana"}, chat
            )
            for i in range(3)
        )
    )
    assert len({o["session_id"] for o in outs}) == 1
    assert len(await services.list_sessions()) == 1 and list(sessions_file(services)) == [f"{CHAT}:ops"]


async def test_existing_default_session_is_kept_when_routes_are_added(
    services: Services, gateway: FakeGateway
) -> None:
    configure(services, None)
    router, chat = InboundRouter(services, "telegram", timeout_s=20), Chat()
    gateway.script.extend([Scripted(text="before"), Scripted(text="after")])
    before = await router.handle({"chat_id": CHAT, "text": "hello", "user": "ana"}, chat)
    configure(services, [OPS], owners=[CHAT])
    after = await router.handle({"chat_id": CHAT, "text": "hello again", "user": "ana"}, chat)
    assert after["session_id"] == before["session_id"]


async def test_approval_prompt_names_the_routed_agent(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    from tests.conftest import write

    d = workdir / ".auoreo" / "tools" / "risky"
    write(
        d / "tool.yaml",
        "name: risky\ndescription: needs approval\nparameters: {type: object, properties: {}}\n"
        "default_action: ask\n",
    )
    write(d / "handler.py", "async def run(args, ctx):\n    return 'risky ran'\n")
    agent = "---\nname: helper\ndescription: test agent\n---\nYou help.\n"
    write(workdir / ".auoreo" / "agents" / "helper" / "AGENT.md", agent)
    services.harness.reload()
    services.settings.teams2.chat_approvals = True
    configure(services, [{**OPS, "agent": "helper"}], owners=[CHAT])
    gateway.script.extend([Scripted(tool_calls=[("risky", {})]), Scripted(text="all done")])
    router, chat = InboundRouter(services, "telegram", timeout_s=20), Chat()
    listing: list[str] = []

    async def send(chat_id: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
        await chat(chat_id, text, meta)
        if text.startswith("Approval needed"):
            rid = text.split("[", 1)[1].split("]", 1)[0]

            async def answer() -> None:
                await router.handle({"chat_id": CHAT, "text": "/pending"}, chat)
                listing.append(chat.sent[-1][1])
                await router.handle({"chat_id": CHAT, "text": f"/approve {rid}"}, chat)

            asyncio.get_running_loop().create_task(answer())
        return {"ok": True}

    out = await router.handle({"chat_id": CHAT, "text": "/ops do the risky thing", "user": "me"}, send)
    texts = [t for _, t, _ in chat.sent]
    prompt = next(t for t in texts if t.startswith("Approval needed"))
    assert "] helper: risky" in prompt
    assert listing and "] helper: risky" in listing[0]
    assert texts[-1] == "[helper] all done" and out["ok"]


def test_saving_a_route_to_a_missing_agent_is_refused(home: Path) -> None:
    from auoreo.core import settings_store
    from auoreo.plugins import config_values as cv

    telegram = _manifest("telegram")
    with pytest.raises(cv.PluginConfigError, match="/routes/0/agent"):
        cv.set_value(home, telegram, "routes", json.dumps([{**OPS, "agent": "ghost"}]), AGENTS)
    broken = [{**OPS, "match": {"field": "text", "regex": "("}}]
    with pytest.raises(cv.PluginConfigError, match="/routes/0"):
        cv.set_value(home, telegram, "routes", json.dumps(broken))
    assert "routes" not in settings_store.read(home).get("plugins", {}).get("telegram", {})
    assert cv.set_value(home, telegram, "routes", json.dumps([OPS]), AGENTS) == [OPS]
    assert settings_store.read(home)["plugins"]["telegram"]["routes"] == [OPS]
