"""H1b: a literal secret in a shell command always asks a human (never yolo / rules / hooks),
with the span masked on the approval card and a "move it to secrets.db" hint (spec §25.10)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from auoreo.core import floors, floors_literal
from auoreo.core.permissions import PermissionEngine, Rule
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.tools.context import ToolContext


@pytest.fixture(autouse=True)
def _yolo_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    import auoreo.core.permissions as perm

    monkeypatch.setattr(perm, "YOLO_FROZEN", True)


ANT = "sk-ant-api03-" + "A1b2C3d4E5f6G7h8I9j0" * 2
STORED = "correct-horse-battery-staple-42"


@pytest.mark.parametrize(
    "cmd",
    [
        f'curl -H "Authorization: Bearer {ANT}" https://api.example.com',
        f"ANTHROPIC_API_KEY={ANT} python app.py",
        "export GITHUB_TOKEN=ghp_" + "x" * 36,
        "psql postgres://admin:hunter2pass@db.internal/app",
        "aws s3 ls # AKIAABCDEFGHIJKLMNOP",
        'echo "-----BEGIN OPENSSH PRIVATE KEY-----" > k',
        'curl -H "Authorization: token abcdefabcdef0123456789" https://x',
        "MY_SERVICE_PASSWORD='p4ssw0rd-literal-value' ./run",
    ],
)
def test_high_confidence_shapes_are_found(cmd: str) -> None:
    spans = floors.literal_secrets(cmd)
    assert spans, cmd
    shown = floors_literal.masked(cmd, spans)
    assert ANT not in shown and "hunter2pass" not in shown and "p4ssw0rd-literal-value" not in shown


@pytest.mark.parametrize(
    "cmd",
    [
        'curl -H "Authorization: Bearer $OPENAI_API_KEY" https://api.example.com',
        "ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY} python app.py",
        "git log --oneline -n 5",
        "echo sk-learn is a package",
        "grep -r TOKEN src/",
        "sort_key=abc python x.py",
    ],
)
def test_references_and_ordinary_text_pass(cmd: str) -> None:
    assert floors.literal_secrets(cmd) == []


def test_known_store_values_match_literally() -> None:
    cmd = f"mysql -p{STORED} -e 'select 1'"
    spans = floors_literal.find(cmd, [STORED, "short"], min_len=12)
    assert [s.kind for s in spans] == ["known"]
    assert floors_literal.masked(cmd, spans) == "mysql -pcorrec…e-42 -e 'select 1'"


async def _run(services: Services, workdir: Path, cmd: str, *, mode: str, interactive: bool = True) -> object:
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home, interactive=interactive)
    engine = PermissionEngine([Rule(permission="bash", pattern="*", action="allow")], mode=mode)  # type: ignore[arg-type]
    return await services.coordinator.runner.executor.run(
        ToolCall("c1", "bash", json.dumps({"command": cmd})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=engine,
    )


async def test_yolo_and_allow_rules_still_ask_with_a_masked_card(services: Services, workdir: Path) -> None:
    services.secrets.set("db/password", STORED)
    cmd = f"echo {STORED} > /dev/null"
    task = asyncio.create_task(_run(services, workdir, cmd, mode="yolo"))
    for _ in range(200):
        if services.approvals.pending():
            break
        await asyncio.sleep(0.02)
    pending = services.approvals.pending()
    assert len(pending) == 1, "a literal secret must reach a human even under yolo + allow *"
    card = pending[0]
    assert STORED not in card.pattern and STORED not in card.detail
    assert "correc…e-42" in card.pattern and "secrets.db" in card.detail and card.one_shot
    await services.approvals.reply(card.request_id, "once")
    out = await task
    assert not out.result.is_error  # type: ignore[attr-defined]
    from sqlalchemy import select

    from auoreo.core.db import AuditRow

    async with services.db.session() as s:
        rows = (await s.execute(select(AuditRow))).scalars().all()
    assert rows and all(STORED not in json.dumps(r.detail, default=str) for r in rows)


async def test_unattended_literal_secret_is_denied_with_the_hint(services: Services, workdir: Path) -> None:
    out = await _run(
        services, workdir, f"curl -H 'Authorization: Bearer {ANT}' x", mode="yolo", interactive=False
    )
    result = out.result  # type: ignore[attr-defined]
    assert result.is_error and "literal secret" in result.content and "secrets.db" in result.content
    assert ANT not in result.content


async def test_plain_command_is_unaffected(services: Services, workdir: Path) -> None:
    out = await _run(services, workdir, "echo plain-$((1+1))", mode="yolo")
    assert not out.result.is_error and "plain-2" in out.result.content  # type: ignore[attr-defined]
