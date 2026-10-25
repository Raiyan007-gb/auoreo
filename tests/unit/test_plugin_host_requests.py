"""Plugin → host requests: namespaced audit rows and gated usage records."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from auoreo.core.db import AuditRow, UsageRow
from auoreo.core.services import Services
from auoreo.plugins.protocol import RpcError
from auoreo.plugins.requests import HOST_REQUESTS


def state(consumes: list[str]) -> Any:
    return SimpleNamespace(name="imagegen", manifest=SimpleNamespace(consumes=consumes), config={})


async def test_audit_record_is_namespaced_redacted_and_capped(services: Services) -> None:
    host = SimpleNamespace(s=services, home=services.settings.home)
    await HOST_REQUESTS["audit.record"](
        host, state([]), {"action": "cost", "detail": {"key": "sk-ant-abcdefghijklmnopqrstuvwxyz"}}
    )
    async with services.db.session() as s:
        row = (await s.execute(select(AuditRow).order_by(AuditRow.seq.desc()))).scalars().first()
    assert row is not None and row.action == "plugin.imagegen.cost" and row.actor == "plugin:imagegen"
    assert "abcdefghijklmnop" not in str(services.audit.plain_detail(row))
    with pytest.raises(RpcError):
        await HOST_REQUESTS["audit.record"](host, state([]), {"action": "Bad Action!", "detail": {}})
    with pytest.raises(RpcError):
        await HOST_REQUESTS["audit.record"](host, state([]), {"action": "big", "detail": {"x": "a" * 5000}})


async def test_usage_record_needs_grant_and_bounds(services: Services) -> None:
    host = SimpleNamespace(s=services, home=services.settings.home)
    params = {"session_id": "ses_x", "model": "openai/gpt-image-1", "cost_usd": 0.04}
    with pytest.raises(RpcError, match="consumes"):
        await HOST_REQUESTS["usage.record"](host, state([]), params)
    await HOST_REQUESTS["usage.record"](host, state(["usage"]), params)
    async with services.db.session() as s:
        row = (await s.execute(select(UsageRow).where(UsageRow.session_id == "ses_x"))).scalar_one()
    assert row.model == "plugin:imagegen/openai/gpt-image-1" and row.cost_usd == pytest.approx(0.04)
    with pytest.raises(RpcError):
        await HOST_REQUESTS["usage.record"](host, state(["usage"]), {**params, "cost_usd": 1e9})
