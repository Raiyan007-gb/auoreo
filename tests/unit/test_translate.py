"""F4 translation: the `translate` tool via the fake gateway, and `/api/voice/transcribe?translate_to`."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext, ToolResult
from tests.audio import tone_wav

FAKES = {"stt": "fake", "tts": "fake", "vad": "fake", "wake": "fake", "desktop": "fake"}


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", backends=dict(FAKES))


async def call(svc: Services, workdir: Path, args: dict) -> ToolResult:  # type: ignore[type-arg]
    engine = PermissionEngine([Rule(permission="read", pattern="*", action="allow")])
    ctx = ToolContext(
        session_id="ses_tr",
        cwd=workdir,
        home=svc.settings.home,
        harness=svc.harness,
        services={"services": svc, "engine": engine},
    )
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", "translate", json.dumps(args)), tools=svc.harness.tools, ctx=ctx, engine=engine
    )
    return out.result


async def test_translate_tool(services: Services, gateway: FakeGateway, workdir: Path) -> None:
    tool = services.harness.tools["translate"]
    assert tool.manifest.exposure == "deferred"
    res = await call(services, workdir, {"text": "Guten Morgen", "target": "en-GB", "source": "de"})
    assert not res.is_error, res.content
    assert res.content == "[en-GB] Guten Morgen"
    req = [r for r in gateway.requests if r.metadata.get("purpose") == "translate"][-1]
    assert req.temperature == 0.0 and req.tool_choice == "none" and req.metadata["target"] == "en-GB"
    assert "en-GB" in str(req.messages[0]["content"]) and "from de" in str(req.messages[0]["content"])
    assert "small" in services.settings.models and req.model == "small"
    assert gateway.script == type(gateway.script)()  # an aux call never eats the main script

    bad = await call(services, workdir, {"text": "hi", "target": "not a tag!"})
    assert bad.is_error and "BCP-47" in bad.content


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


async def test_transcribe_translate_to(client: httpx.AsyncClient) -> None:
    wav = tone_wav(1.0, comment="bonjour tout le monde")
    r = await client.post(
        "/api/voice/transcribe", data={"translate_to": "en"}, files={"file": ("a.wav", wav, "audio/wav")}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "bonjour tout le monde" and body["translation"] == "[en] bonjour tout le monde"
    plain = await client.post("/api/voice/transcribe", files={"file": ("a.wav", wav, "audio/wav")})
    assert plain.json()["translation"] is None
    bad = await client.post(
        "/api/voice/transcribe", params={"translate_to": "??"}, files={"file": ("a.wav", wav, "audio/wav")}
    )
    assert bad.status_code == 422 and "BCP-47" in bad.text
