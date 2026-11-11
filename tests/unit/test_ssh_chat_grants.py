"""M7 hosts-b D4: who may grant `ssh` and `sftp` (`m7.ssh.chat_grants`, default off).

A chat `/approve` (even by id) never answers a `local_only` card; `/deny` still does. A
platform session without a rule is refused at once instead of waiting for a card nobody can
answer; unattended approval never covers `ssh` or `sftp`."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.plugins.chat_approvals import ApprovalNotifier
from auoreo.plugins.connectors import InboundRouter
from auoreo.tools import gates
from auoreo.tools.environments import pool_for
from tests.unit import test_env_ssh
from tests.unit.test_chat_approvals import OWNER, Chat, pair
from tests.unit.test_env_ssh import BASE, WHY, SshServer, audit_rows, host_file, session, tool

ssh_server = test_env_ssh.ssh_server
needs_server = test_env_ssh.pytestmark


async def local_card(services: Services, name: str = "prod", sid: str = "sA") -> asyncio.Task[Any]:
    task = asyncio.create_task(
        services.approvals.request(
            sid, "use_environment", "ssh", name, f"ssh ops@10.0.0.9:22 ({name})",
            one_shot=True, justification=WHY, local_only=True,
        )
    )  # fmt: skip
    for _ in range(50):
        if any(p.pattern == name for p in services.approvals.pending()):
            break
        await asyncio.sleep(0.01)
    return task


async def test_chat_never_approves_a_local_only_card(services: Services) -> None:
    pair(services.settings.home, OWNER)
    router, chat = InboundRouter(services, "telegram"), Chat()
    card = await local_card(services)
    rid = services.approvals.pending()[0].request_id
    for text in ("/approve", "/approve all", f"/approve {rid[:8]}", f"/approve {rid}"):
        await router.handle({"chat_id": OWNER, "text": text}, chat)
        assert not card.done(), text
        assert "web UI only" in chat.sent[-1][1]
    rows = [r for r in await audit_rows(services, "approval.chat") if r.decision == "deny"]
    assert len(rows) == 2 and {r.reason for r in rows} == {"local_only"}
    assert rows[0].detail["permission"] == "ssh"
    await router.handle({"chat_id": OWNER, "text": f"/deny {rid[:8]} no"}, chat)
    assert not (await card).granted


async def test_other_cards_still_answered_beside_a_local_only_one(services: Services) -> None:
    pair(services.settings.home, OWNER)
    router, chat = InboundRouter(services, "telegram"), Chat()
    card = await local_card(services)
    other = asyncio.create_task(services.approvals.request("sB", "bash", "bash", "echo ok", "d"))
    await asyncio.sleep(0.05)
    await router.handle({"chat_id": OWNER, "text": "/approve all"}, chat)
    assert (await other).granted and not card.done()
    assert "Approved 1" in chat.sent[-1][1] and "web UI only" in chat.sent[-1][1]
    await services.approvals.reply(services.approvals.pending()[0].request_id, "reject")
    await card


async def test_notifier_tells_the_chat_to_use_the_web_ui(services: Services) -> None:
    sent: list[str] = []

    async def send(chat_id: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
        sent.append(text)
        return {"ok": True}

    async with ApprovalNotifier(services, "sA", OWNER, send):
        card = await local_card(services)
        for _ in range(100):
            if sent:
                break
            await asyncio.sleep(0.02)
    assert sent and "web UI only" in sent[0] and "/approve" not in sent[0]
    await services.approvals.reply(services.approvals.pending()[0].request_id, "reject")
    await card


@needs_server
async def test_gate_marks_ssh_cards_local_only_and_web_reply_grants(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    services.settings.m7.ssh.hosts = True
    host_file(services, ssh_server)
    pair(services.settings.home, OWNER)
    engine = PermissionEngine(BASE)
    ctx = session(services, workdir, engine, interactive=True)
    call = asyncio.create_task(
        tool(services, ctx, engine, "use_environment", {"name": "lab", "justification": WHY})
    )
    for _ in range(300):
        if services.approvals.pending():
            break
        await asyncio.sleep(0.01)
    card = services.approvals.pending()[0]
    assert card.local_only and card.one_shot
    chat = Chat()
    await InboundRouter(services, "telegram").handle(
        {"chat_id": OWNER, "text": f"/approve {card.request_id}"}, chat
    )
    assert not call.done() and "web UI only" in chat.sent[-1][1]
    await services.approvals.reply(card.request_id, "once")  # the web UI / REST
    text, err, _ = await call
    assert not err, text
    await pool_for(services).close()


@needs_server
async def test_chat_grants_on_restores_answer_by_id(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    services.settings.m7.ssh.hosts = True
    services.settings.m7.ssh.chat_grants = True
    host_file(services, ssh_server)
    pair(services.settings.home, OWNER)
    engine = PermissionEngine(BASE)
    ctx = session(services, workdir, engine, interactive=True)
    call = asyncio.create_task(
        tool(services, ctx, engine, "use_environment", {"name": "lab", "justification": WHY})
    )
    for _ in range(300):
        if services.approvals.pending():
            break
        await asyncio.sleep(0.01)
    card = services.approvals.pending()[0]
    assert not card.local_only
    chat = Chat()
    await InboundRouter(services, "telegram").handle(
        {"chat_id": OWNER, "text": f"/approve {card.request_id}"}, chat
    )
    text, err, _ = await call
    assert not err, text
    await pool_for(services).close()


@needs_server
async def test_platform_session_without_a_rule_is_refused_at_once(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    services.settings.m7.ssh.hosts = True
    host_file(services, ssh_server)
    engine = PermissionEngine(BASE)
    ctx = session(services, workdir, engine, interactive=True)
    ctx.services["unattended"] = "platform"
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "lab", "justification": WHY})
    assert err and "messaging-app session cannot grant" in text and "ssh lab" in text
    assert not services.approvals.pending() and not ssh_server.locals
    engine = PermissionEngine([*BASE, Rule(permission="ssh", pattern="lab", action="allow")])
    ctx = session(services, workdir, engine, sid="s2", interactive=True)
    ctx.services["unattended"] = "platform"
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "lab"})
    assert not err, text
    await pool_for(services).close()


@needs_server
async def test_unattended_approve_never_grants_ssh(
    services: Services, ssh_server: SshServer, workdir: Path
) -> None:
    services.settings.m7.ssh.hosts = True
    services.settings.approvals.unattended.cron = "approve"
    host_file(services, ssh_server)
    engine = PermissionEngine(BASE)
    ctx = session(services, workdir, engine)  # non-interactive
    ctx.services["unattended"] = "cron"
    text, err, _ = await tool(services, ctx, engine, "use_environment", {"name": "lab", "justification": WHY})
    assert err and "non-interactive" in text and not ssh_server.locals


async def test_sftp_is_never_auto_approved(services: Services, workdir: Path) -> None:
    services.settings.approvals.unattended.cron = "approve"
    assert "sftp" in gates.NEVER_AUTO_APPROVE
    ex = SimpleNamespace(s=services, _err=lambda call, msg: SimpleNamespace(error=msg))
    ctx = SimpleNamespace(session_id="s1", services={"unattended": "cron"})
    t = SimpleNamespace(name="env_transfer")
    out = await gates.unattended_gate(
        ex, ToolCall("c", "env_transfer", "{}"), t, ctx, "sftp", "lab:download:/x", {}, ""
    )  # type: ignore[arg-type]
    assert out is not None and "never auto-approved" in out.error
    out = await gates.unattended_gate(ex, ToolCall("c", "x", "{}"), t, ctx, "bash", "ls", {}, "")  # type: ignore[arg-type]
    assert out is None  # the setting still applies to ordinary asks
