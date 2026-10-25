"""`/api/voice/*` with the fake stt/tts/vad backends (push-to-talk path, M4c)."""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.voice import audio
from tests.audio import silence_wav, tone_wav
from tests.integration.test_api import parse_sse, wait_done

FAKES = {"stt": "fake", "tts": "fake", "vad": "fake", "wake": "fake", "desktop": "fake"}
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=5, backends=dict(FAKES))


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def events(client: httpx.AsyncClient, **params: str) -> list[dict]:  # type: ignore[type-arg]
    r = await client.get("/api/events", params={"follow": "false", **params})
    return parse_sse(r.text)


@needs_ffmpeg
async def test_transcribe_with_session_runs_a_followup(client: httpx.AsyncClient, services: Services) -> None:
    sess = await services.create_session(title="voice")
    wav = tone_wav(1.5, rate=48_000, channels=2, comment="turn on the lights")
    r = await client.post(
        "/api/voice/transcribe",
        params={"session_id": sess.id},
        files={"file": ("ptt.wav", wav, "audio/wav")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["text"] == "turn on the lights" and body["backend"] == "fake"
    assert body["duration_s"] == pytest.approx(1.5, abs=0.02)
    assert body["segments"][0]["text"] == "turn on the lights"
    assert body["run_id"].startswith("run_")
    await wait_done(services, sess.id)
    evs = await events(client, session_id=sess.id)
    kinds = [e["type"] for e in evs]
    assert "voice.transcribed" in kinds and "run.completed" in kinds
    done = [e for e in evs if e["type"] == "run.completed"][-1]
    assert done["data"]["text"] == "echo: turn on the lights"
    vt = next(e for e in evs if e["type"] == "voice.transcribed")
    assert vt["data"] == {
        "session_id": sess.id,
        "text": "turn on the lights",
        "duration_s": 1.5,
        "backend": "fake",
    }


async def test_transcribe_without_session_and_form_language(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/voice/transcribe",
        data={"language": "fr"},
        files={"file": ("a.wav", tone_wav(2.0), "audio/wav")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "fake transcript (2.0 s)" and r.json()["language"] == "fr"
    assert r.json()["run_id"] is None
    assert any(
        e["type"] == "voice.transcribed" and e["aggregate_id"] == "server" for e in await events(client)
    )


async def test_silence_is_422_no_speech(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/voice/transcribe", files={"file": ("s.wav", silence_wav(1.0), "audio/wav")})
    assert r.status_code == 422 and r.json()["detail"] == "no speech detected"


@needs_ffmpeg
async def test_garbage_is_422_and_unknown_session_404(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/voice/transcribe", files={"file": ("x.webm", b"nope" * 100, "audio/webm")})
    assert r.status_code == 422 and "could not decode" in r.json()["detail"]
    r = await client.post(
        "/api/voice/transcribe",
        params={"session_id": "ses_nope"},
        files={"file": ("a.wav", tone_wav(), "audio/wav")},
    )
    assert r.status_code == 404


async def test_oversized_upload_is_413(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "MAX_INPUT_BYTES", 1024)
    r = await client.post("/api/voice/transcribe", files={"file": ("a.wav", tone_wav(1.0), "audio/wav")})
    assert r.status_code == 413


async def test_speak_returns_wav(client: httpx.AsyncClient) -> None:
    text = "Hello **there**, this is a spoken reply! 🎉"
    r = await client.post("/api/voice/speak", json={"text": text})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "audio/wav" and r.headers["x-voice-backend"] == "fake"
    assert r.content[:4] == b"RIFF"
    spoken = audio.read_wav(r.content).comment
    assert spoken == "Hello there, this is a spoken reply!"
    assert audio.duration_s(r.content) == pytest.approx(len(spoken) / 15, abs=0.01)
    evs = await events(client)
    spoke = [e for e in evs if e["type"] == "voice.spoke"][-1]
    assert spoke["data"] == {"chars": len(spoken), "backend": "fake"}


@needs_ffmpeg
async def test_speak_transcodes_on_request(client: httpx.AsyncClient) -> None:
    r = await client.post("/api/voice/speak", json={"text": "Short reply.", "format": "opus"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/ogg" and r.content[:4] == b"OggS"
    r = await client.post("/api/voice/speak", json={"text": "Short reply.", "format": "mp3"})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"


async def test_speak_long_text_is_chunked_and_concatenated(
    client: httpx.AsyncClient, services: Services
) -> None:
    tts = services.backend("tts")
    tts.chunk_limit = 50
    text = " ".join(f"Sentence number {i} is here." for i in range(10))
    r = await client.post("/api/voice/speak", json={"text": text})
    assert r.status_code == 200
    assert len(tts.calls) > 1 and all(len(c["text"]) <= 50 for c in tts.calls)
    assert audio.read_wav(r.content).comment == text


async def test_speak_limits(client: httpx.AsyncClient) -> None:
    assert (await client.post("/api/voice/speak", json={"text": "x" * 5001})).status_code == 422
    assert (await client.post("/api/voice/speak", json={"text": "hi", "speed": 9})).status_code == 422
    r = await client.post("/api/voice/speak", json={"text": "🎉🎉"})
    assert r.status_code == 422 and "nothing to speak" in r.json()["detail"]


async def test_status_lists_backends(client: httpx.AsyncClient) -> None:
    r = await client.get("/api/voice/status")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    rows = {b["kind"]: b for b in body["backends"]}
    assert set(rows) == {"stt", "tts", "vad", "wake", "desktop"}
    assert all(b["ok"] and b["name"] == "fake" for b in rows.values()), json.dumps(rows)


async def test_status_reports_real_backend_not_ready(home: Path, workdir: Path, gateway: FakeGateway) -> None:
    st = Settings(home=home, gateway="fake", backends={"stt": "faster_whisper"})
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    app = create_app(st, services=svc)
    try:
        async with (
            LifespanManager(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
        ):
            rows = {b["kind"]: b for b in (await c.get("/api/voice/status")).json()["backends"]}
            assert rows["stt"]["name"] == "faster_whisper" and rows["stt"]["ok"] is False
            assert rows["stt"]["remedy"]
            r = await c.post("/api/voice/transcribe", files={"file": ("a.wav", tone_wav(), "audio/wav")})
            assert r.status_code == 503 and "faster-whisper" in r.json()["detail"]
    finally:
        await svc.close()


async def test_voice_disabled_is_503(home: Path, workdir: Path, gateway: FakeGateway) -> None:
    st = Settings(home=home, gateway="fake", voice_enabled=False)
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    app = create_app(st, services=svc)
    try:
        async with (
            LifespanManager(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
        ):
            assert (await c.get("/api/voice/status")).json() == {"enabled": False, "backends": []}
            assert (await c.post("/api/voice/speak", json={"text": "hi"})).status_code == 503
    finally:
        await svc.close()
