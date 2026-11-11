"""M7 W3: `memory_tenant` on an agent → an `agent:<slug>` partition of the fact store.

Design: docs/designs/m7-agents-bots.md (CUJ 3). MEMORY.md / USER.md / PREFS.md stay shared.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from auoreo.core.db import AuditRow
from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.harness.manifests import AgentManifest
from auoreo.harness.trust import sanitize_agent_manifest
from auoreo.memory.recall import memory_for, memory_for_agent_name, recall_block
from auoreo.memory.service_i4 import TenantDenied
from auoreo.tools.context import ToolContext
from tests.conftest import write
from tests.unit.test_tools_m2 import call, ctx_for

AGENT = "---\nname: {name}\ndescription: test agent\n{extra}---\nYou are {name}.\n"


def _agents(services: Services, workdir: Path, **tenants: str | None) -> None:
    for name, tenant in tenants.items():
        extra = f"memory_tenant: {tenant}\n" if tenant else ""
        write(workdir / ".auoreo" / "agents" / name / "AGENT.md", AGENT.format(name=name, extra=extra))
    services.harness.reload()
    assert not services.harness.errors, services.harness.errors


def _on(services: Services) -> None:
    services.settings.m7.agents.memory_tenants = True


def _manifest(tenant: str | None = "support") -> AgentManifest:
    return AgentManifest(name="x", description="d", memory_tenant=tenant)


def test_manifest_field_is_a_bounded_slug() -> None:
    assert _manifest().memory_tenant == "support"
    assert AgentManifest(name="x", description="d").memory_tenant is None
    for bad in ("team:1", "agent:other", "Has Space", "../up", "", "a" * 49):
        with pytest.raises(ValidationError):
            _manifest(bad)


def test_untrusted_project_agent_loses_its_tenant() -> None:
    assert sanitize_agent_manifest(_manifest()).memory_tenant is None
    plain = AgentManifest(name="x", description="d")
    assert sanitize_agent_manifest(plain) is plain


async def test_flag_off_keeps_the_local_service_and_provisions_nothing(services: Services) -> None:
    mem = services.memory
    assert mem is not None and services.settings.m7.agents.memory_tenants is False
    assert await mem.for_agent(_manifest()) is mem
    assert await memory_for(services, _manifest()) is mem
    assert mem.store.tenant_ids() == ["local"]


async def test_flag_on_without_a_tenant_is_local(services: Services) -> None:
    _on(services)
    mem = services.memory
    assert mem is not None
    assert await mem.for_agent(_manifest(None)) is mem
    assert mem.store.tenant_ids() == ["local"]


async def test_tenant_is_provisioned_once_audited_and_cached(services: Services) -> None:
    _on(services)
    mem = services.memory
    assert mem is not None
    svc = await mem.for_agent(_manifest())
    assert svc is not mem and svc.store.tenant == "agent:support"
    assert await mem.for_agent(_manifest()) is svc  # another agent naming the same tenant shares it
    assert mem.store.tenant_ids() == ["agent:support", "local"]
    async with services.db.session() as s:
        q = select(AuditRow).where(AuditRow.action == "memory.tenant_provisioned")
        rows = list((await s.execute(q)).scalars())
    assert [services.audit.plain_detail(r)["tenant"] for r in rows] == ["agent:support"]


async def test_facts_are_isolated_in_both_directions(services: Services) -> None:
    _on(services)
    mem = services.memory
    assert mem is not None
    a = await mem.for_agent(_manifest("support"))
    b = await mem.for_agent(_manifest("coding"))
    await a.add_fact("refund window for premium customers is forty days")
    await b.add_fact("the deploy pipeline needs the staging token rotated weekly")
    await mem.add_fact("the office plants are watered on mondays")

    async def texts(svc: object, query: str) -> str:
        return " ".join(h.text for h in await svc.search(query, kinds=("fact",)))  # type: ignore[attr-defined]

    assert "forty days" in await texts(a, "refund window premium")
    assert "forty days" not in await texts(b, "refund window premium")
    assert "forty days" not in await texts(mem, "refund window premium")
    assert "staging token" not in await texts(a, "deploy pipeline staging token")
    assert "mondays" not in await texts(a, "office plants watered")
    assert "mondays" in await texts(mem, "office plants watered")
    assert "forty days" in await a.context_block("refund window premium")
    assert "forty days" not in await mem.context_block("refund window premium")
    assert "forty days" not in await b.context_block("refund window premium")


async def test_memory_tool_and_searches_use_the_agent_tenant(services: Services, workdir: Path) -> None:
    _on(services)
    _agents(services, workdir, support="support", helper="support", plain=None)
    mem = services.memory
    assert mem is not None

    async def ctx(agent: str) -> ToolContext:
        c = ctx_for(services, workdir, agent=agent)
        c.services["memory"] = await memory_for(services, services.harness.agent(agent).manifest)
        return c

    op = {"action": "add", "target": "fact", "content": "Escalations go to the tier three queue"}
    text, err = await call(services, await ctx("support"), "memory", {"operations": [op]})
    assert not err, text
    assert [r["content"] for r in mem.store.fact_rows()] == []  # nothing in `local`
    q = {"query": "escalations tier three queue"}
    text, err = await call(services, await ctx("support"), "knowledge_search", q)
    assert not err and "tier three" in text
    text, err = await call(services, await ctx("helper"), "knowledge_search", q)
    assert not err and "tier three" in text  # same tenant, another agent
    text, err = await call(services, await ctx("plain"), "knowledge_search", q)
    assert "tier three" not in text
    # the reverse direction: a local fact is invisible to the tenanted agent
    await mem.add_fact("The quarterly offsite is in Lisbon")
    text, _ = await call(services, await ctx("support"), "knowledge_search", {"query": "quarterly offsite"})
    assert "Lisbon" not in text
    # an explicit cross-tenant read is refused from either side
    bad = {"query": "escalations", "tenant": "agent:support"}
    text, err = await call(services, await ctx("plain"), "knowledge_search", bad)
    assert err and "tenant-deny" in text
    text, err = await call(services, await ctx("support"), "knowledge_search", {**bad, "tenant": "local"})
    assert err and "tenant-deny" in text


@pytest.mark.parametrize(
    "bad", ["agent:a|b", "agent:a|", "agent:A", "agent:", "agent:-x", "", "Local", "other"]
)
def test_store_refuses_a_tenant_tag_that_is_not_injective(tmp_path: Path, bad: str) -> None:
    """The transcript partition of `agent:<slug>` is the prefix `agent:<slug>|` on the session
    id, so `agent:a` would also see `agent:a|b`: only manifest slugs are accepted."""
    from auoreo.memory.store import MemoryStore

    with pytest.raises(ValueError, match="tenant"):
        MemoryStore(tmp_path / "memory.db", 8, tenant=bad)


def test_store_tenants_stay_apart_in_transcripts(tmp_path: Path) -> None:
    from auoreo.memory.store import MemoryStore

    names = ["local", "agent:a", "agent:a-b", "agent:a.b", "agent:ab", "team:t1", "team:t_0123abcd"]
    stores = {n: MemoryStore(tmp_path / "memory.db", 8, tenant=n) for n in names}
    for n, s in stores.items():
        s.add_transcript("sess-shared", "user", f"secretword {n}")
    for n in ("agent:a", "agent:a-b", "agent:a.b", "agent:ab"):
        rows = stores[n].search_transcript("secretword", 50, None)
        assert sorted(r.text.split()[1] for r in rows) == [n]
    for s in stores.values():
        s.close()


async def test_topic_files_are_refused_to_a_tenanted_agent(services: Services, workdir: Path) -> None:
    """Topic files (`~/.auoreo/memory/topics/*.md` + a MEMORY.md index line) are one shared
    folder with no tenant: a tenanted agent may neither write nor read them, so what it learns
    stays in its fact store and it cannot read what other agents filed there."""
    _on(services)
    _agents(services, workdir, support="support", plain=None)

    async def ctx(agent: str) -> ToolContext:
        c = ctx_for(services, workdir, agent=agent)
        c.services["memory"] = await memory_for(services, services.harness.agent(agent).manifest)
        return c

    topics = services.settings.home / "memory" / "topics"
    secret = {"action": "add", "target": "topic", "name": "customer_acme", "description": "acme"}
    op = {**secret, "content": "ACME renewal price 120k"}
    text, err = await call(services, await ctx("support"), "memory", {"operations": [op]})
    assert err and "fact" in text and not (topics / "customer_acme.md").exists()
    # an untenanted agent still files topics; the tenanted one cannot read or list them
    text, err = await call(services, await ctx("plain"), "memory", {"operations": [op]})
    assert not err and (topics / "customer_acme.md").exists(), text
    text, err = await call(services, await ctx("support"), "memory_read", {"name": "customer_acme"})
    assert err and "120k" not in text and "fact" in text
    text, err = await call(services, await ctx("support"), "memory_read", {"name": "topics/customer_acme.md"})
    assert err and "120k" not in text
    text, err = await call(services, await ctx("support"), "memory_read", {})
    assert not err and "customer_acme" not in text
    text, err = await call(services, await ctx("plain"), "memory_read", {"name": "customer_acme"})
    assert not err and "120k" in text
    # flag off: no tenants, topics work as before
    services.settings.m7.agents.memory_tenants = False
    text, err = await call(services, await ctx("support"), "memory_read", {"name": "customer_acme"})
    assert not err and "120k" in text


async def test_agent_namespace_is_never_requestable_from_another_tenant(services: Services) -> None:
    _on(services)
    mem = services.memory
    assert mem is not None
    a = await mem.for_agent(_manifest("support"))
    await mem.for_agent(_manifest("coding"))
    for svc, wanted in ((mem, "agent:support"), (a, "agent:coding"), (a, "local")):
        with pytest.raises(TenantDenied):
            await svc.authorize_tenant(wanted, session_id=None, tool="knowledge_search")
    await a.authorize_tenant("agent:support", session_id=None, tool="knowledge_search")


async def test_runner_records_and_recalls_in_the_tenant(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    _on(services)
    _agents(services, workdir, support="support")
    mem = services.memory
    assert mem is not None
    tenant = await mem.for_agent(services.harness.agent("support").manifest)
    await tenant.add_fact("Warranty claims need the purchase receipt number")
    await mem.add_fact("Warranty claims are handled by the Lisbon office")
    sess = await services.create_session(agent="support")
    gateway.queue(Scripted(text="Ask for the zebra receipt."))
    await services.coordinator.submit(sess.id, "how do warranty claims work", interactive=False)
    await services.coordinator.wait(sess.id, 30)
    main = [r for r in gateway.requests if r.metadata.get("purpose") not in {"title", "compaction"}]
    sent = str(main[0].messages)
    assert "purchase receipt number" in sent and "Lisbon office" not in sent
    assert await tenant.search_sessions("warranty claims", session_id=sess.id)
    assert await tenant.search_sessions("zebra receipt", session_id=sess.id)
    assert not await mem.search_sessions("warranty claims", session_id=sess.id)
    assert not await mem.search_sessions("zebra receipt", session_id=sess.id)
    hit = (await tenant.search_sessions("zebra receipt"))[0]
    assert hit.citation == f"session:{sess.id}"
    row = tenant.store.search_transcript("zebra receipt", 5, None)[0]
    assert row.extra["role"] == "assistant"  # the tenant tag on the stored role is not exposed


async def test_runner_flag_off_stays_local(services: Services, gateway: FakeGateway, workdir: Path) -> None:
    _agents(services, workdir, support="support")
    mem = services.memory
    assert mem is not None
    sess = await services.create_session(agent="support")
    gateway.queue(Scripted(text="Ask for the zebra receipt."))
    await services.coordinator.submit(sess.id, "how do warranty claims work", interactive=False)
    await services.coordinator.wait(sess.id, 30)
    assert await mem.search_sessions("warranty claims", session_id=sess.id)
    assert mem.store.tenant_ids() == ["local"]


async def test_session_lookup_for_compaction_and_recall_helper(services: Services, workdir: Path) -> None:
    _on(services)
    _agents(services, workdir, support="support")
    mem = services.memory
    assert mem is not None
    svc = await memory_for_agent_name(services, "support")
    assert svc is not None and svc.store.tenant == "agent:support"
    assert await memory_for_agent_name(services, "default") is mem
    assert await memory_for_agent_name(services, "no-such-agent") is mem
    await svc.record_message("s9", "compaction", "summary: the customer wanted a turquoise kettle")
    assert await svc.search_sessions("turquoise kettle") and not await mem.search_sessions("turquoise kettle")
    # recall never raises and honours `memory: false`
    assert await recall_block(services, None, None, _manifest(), "anything", 8) == ""
    off = AgentManifest(name="x", description="d", memory=False)
    assert await recall_block(services, svc, None, off, "turquoise kettle", 8) == ""


async def test_failed_provisioning_fails_closed(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    _on(services)
    mem = services.memory
    assert mem is not None

    async def boom(*_a: object, **_k: object) -> bool:
        raise RuntimeError("disk full")

    monkeypatch.setattr(type(mem), "provision_tenant", boom)
    assert await memory_for(services, _manifest()) is None  # never falls back to `local`


async def test_team_session_with_a_tenanted_agent_uses_the_agent_tenant(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    _on(services)
    _agents(services, workdir, support="support")
    mem = services.memory
    assert mem is not None
    lead = await services.create_session(agent="support")
    team = await services.ext["teams"].create_team(lead.id, "alpha")
    await services.ext["tenants"].drain()
    gateway.queue(Scripted(text="noted the walrus plan"))
    await services.coordinator.submit(lead.id, "remember the walrus plan", interactive=False)
    await services.coordinator.wait(lead.id, 30)
    tenant = await mem.for_agent(services.harness.agent("support").manifest)
    assert await tenant.search_sessions("walrus plan", session_id=lead.id)
    assert not await mem.for_tenant(f"team:{team.id}").search_sessions("walrus plan")
    await tenant.authorize_tenant(f"team:{team.id}", session_id=lead.id, tool="knowledge_search")
