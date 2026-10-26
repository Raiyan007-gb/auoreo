"""H2: `/approve`, `/deny [reason]`, `/approve all`, `/pending` from messaging connectors (spec §19.9)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from auoreo.core.services import Services
from auoreo.core.settings import write_settings_value
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.plugins.chat_approvals import is_command
from auoreo.plugins.connectors import InboundRouter
from tests.conftest import write

OWNER, STRANGER = "111", "999"


class Chat:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def __call__(self, chat_id: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
        self.sent.append((chat_id, text))
        return {"ok": True, "detail": "sent"}


def pair(home: Path, *chats: str) -> None:
    write_settings_value(home, "plugins.telegram.allowlist", list(chats))


async def ask(services: Services, tool: str = "bash", pattern: str = "rm -rf build") -> asyncio.Task[Any]:
    # one session per request: a reject cascades to the other pendings of the same session
    task = asyncio.create_task(services.approvals.request(f"s-{pattern}", tool, tool, pattern, "detail"))
    for _ in range(50):
        if any(p.pattern == pattern for p in services.approvals.pending()):
            break
        await asyncio.sleep(0.01)
    return task


def test_is_command() -> None:
    assert is_command("/approve") and is_command("/deny too risky") and is_command("/approve@auoreo_bot all")
    assert (
        is_command("/pending")
        and not is_command("approve")
        and not is_command("/start")
        and not is_command("")
    )


async def test_owner_approves_fifo_and_denies_with_reason(services: Services) -> None:
    pair(services.settings.home, OWNER, "222")
    router, chat = InboundRouter(services, "telegram"), Chat()
    first, second = await ask(services, pattern="first"), await ask(services, pattern="second")

    out = await router.handle({"chat_id": OWNER, "text": "/pending"}, chat)
    assert out["command"] and "first" in chat.sent[-1][1] and "second" in chat.sent[-1][1]
    await router.handle({"chat_id": OWNER, "text": "/approve"}, chat)  # FIFO: the oldest
    assert (await first).granted and "Approved 1" in chat.sent[-1][1]
    await router.handle({"chat_id": OWNER, "text": "/deny too risky"}, chat)
    res = await second
    assert not res.granted and res.message == "too risky" and "Denied 1" in chat.sent[-1][1]
    await router.handle({"chat_id": OWNER, "text": "/approve"}, chat)
    assert chat.sent[-1][1] == "No approvals are pending."
    assert await services.list_sessions() == []  # commands never start a session run


async def test_targeted_by_id_and_approve_all(services: Services) -> None:
    pair(services.settings.home, OWNER)
    router, chat = InboundRouter(services, "telegram"), Chat()
    a, b, c = [await ask(services, pattern=x) for x in ("a", "b", "c")]
    b_id = next(p.request_id for p in services.approvals.pending() if p.pattern == "b")
    await router.handle({"chat_id": OWNER, "text": f"/deny {b_id[:8]} not this one"}, chat)
    res_b = await b
    assert not res_b.granted and res_b.message == "not this one"
    assert not a.done() and not c.done()
    await router.handle({"chat_id": OWNER, "text": "/approve all"}, chat)
    assert (await a).granted and (await c).granted and "Approved 2" in chat.sent[-1][1]


async def test_only_paired_owner_identities_may_answer(services: Services) -> None:
    pair(services.settings.home, OWNER, "222")
    write_settings_value(services.settings.home, "plugins.telegram.owners", [OWNER])
    router, chat = InboundRouter(services, "telegram"), Chat()
    pending = await ask(services)
    for who in (STRANGER, "222"):  # unpaired, and paired-but-not-owner
        await router.handle({"chat_id": who, "text": "/approve"}, chat)
        assert "Only a paired owner" in chat.sent[-1][1]
    assert not pending.done()
    await router.handle({"chat_id": OWNER, "text": "/approve"}, chat)
    assert (await pending).granted


async def test_chat_approvals_mode_pushes_requests_and_resumes_run(
    services: Services, gateway: FakeGateway, workdir: Path
) -> None:
    d = workdir / ".auoreo" / "tools" / "risky"
    write(
        d / "tool.yaml",
        "name: risky\ndescription: needs approval\nparameters: {type: object, properties: {}}\n"
        "default_action: ask\n",
    )
    write(d / "handler.py", "async def run(args, ctx):\n    return 'risky ran'\n")
    services.harness.reload()
    services.settings.teams2.chat_approvals = True
    pair(services.settings.home, OWNER)
    gateway.script.extend([Scripted(tool_calls=[("risky", {})]), Scripted(text="all done")])
    router = InboundRouter(services, "telegram", timeout_s=20)

    async def send(chat_id: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
        chat.sent.append((chat_id, text))
        if text.startswith("Approval needed"):
            rid = text.split("[", 1)[1].split("]", 1)[0]
            asyncio.get_running_loop().create_task(
                router.handle({"chat_id": OWNER, "text": f"/approve {rid}"}, chat)
            )
        return {"ok": True}

    chat = Chat()
    out = await router.handle({"chat_id": OWNER, "text": "please do the risky thing", "user": "me"}, send)
    texts = [t for _, t in chat.sent]
    assert any(t.startswith("Approval needed") and "risky" in t for t in texts)
    assert any(t.startswith("Approved 1") for t in texts)
    assert texts[-1] == "all done" and out["ok"]
