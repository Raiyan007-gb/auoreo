"""Smoke test for the generated Python SDK (`sdk/python/auoreo_client`) + the extras layer."""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings

SDK = Path(__file__).resolve().parents[2] / "sdk" / "python"
if not (SDK / "auoreo_client" / "__init__.py").is_file():
    pytest.skip("generated SDK absent (run scripts/gen_sdk.sh)", allow_module_level=True)
sys.path.insert(0, str(SDK))

from auoreo_client import AuthenticatedClient  # type: ignore[import-not-found]  # noqa: E402
from auoreo_client.api.health import health_api_health_get  # type: ignore[import-not-found]  # noqa: E402
from auoreo_client.api.sessions import (  # type: ignore[import-not-found]  # noqa: E402
    create_session_api_sessions_post,
    get_session_api_sessions_session_id_get,
)
from auoreo_client.models import SessionCreate  # type: ignore[import-not-found]  # noqa: E402
from auoreo_sdk_extras import chat_and_wait, subscribe_events  # type: ignore[import-not-found]  # noqa: E402


@pytest.fixture
async def app(services: Services, settings: Settings) -> AsyncIterator[Any]:
    settings.server_password = "pw"
    application = create_app(settings, services=services)
    async with LifespanManager(application):
        yield application


async def test_generated_client_health_and_session(app: Any) -> None:
    transport = httpx.ASGITransport(app=app)
    client = AuthenticatedClient(base_url="http://127.0.0.1", token="pw")
    client.set_async_httpx_client(
        httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1", headers={"Authorization": "Bearer pw"}
        )
    )
    health = await health_api_health_get.asyncio(client=client)
    assert health is not None and health.status == "ok"
    created = await create_session_api_sessions_post.asyncio(client=client, body=SessionCreate(title="sdk"))
    assert created is not None and created.id.startswith("ses_")
    fetched = await get_session_api_sessions_session_id_get.asyncio(created.id, client=client)
    assert fetched is not None and fetched.title == "sdk"

    text = await chat_and_wait(client, "hello sdk", session_id=created.id, timeout_s=20, poll_s=0.05)
    assert text == "echo: hello sdk"

    seen: list[dict[str, Any]] = []
    last = await subscribe_events(
        "http://127.0.0.1",
        "pw",
        0,
        seen.append,
        session_ids=[created.id],
        follow=False,
        transport=transport,
    )
    kinds = [e["type"] for e in seen]
    assert kinds[0] == "session.created" and "run.completed" in kinds
    assert last == seen[-1]["seq"]
    resumed: list[dict[str, Any]] = []
    mid = seen[1]["seq"]
    await subscribe_events(
        "http://127.0.0.1",
        "pw",
        mid,
        resumed.append,
        session_ids=[created.id],
        follow=False,
        transport=transport,
    )
    assert [e["seq"] for e in resumed] == [e["seq"] for e in seen if e["seq"] > mid]
    await client.get_async_httpx_client().aclose()
