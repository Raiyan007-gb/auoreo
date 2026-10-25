"""F6: the `auoreo voice-host` media sidecar, end-to-end through a real subprocess (fake inner
backends): round trip, crash → restart, crash loop → honest failure, timeout, payload cap,
env scrub, and `/api/voice/transcribe` through the sidecar backends."""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.harness.discovery import Root
from auoreo.harness.registry import Harness
from auoreo.tools.context import ToolError
from auoreo.voice import sidecar
from auoreo.voice.host import MAX_PAYLOAD_BYTES, decode_payload
from auoreo.voice.sidecar import VoiceHost, VoiceHostError
from tests.audio import tone_wav
from tests.conftest import REPO

DEBUG = {"AUOREO_VOICE_HOST_DEBUG": "1"}
SIDECARS = {"stt": "sidecar", "tts": "sidecar", "vad": "sidecar", "wake": "fake", "desktop": "fake"}


@pytest.fixture
def st(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", backends=dict(SIDECARS), voice_host_timeout_s=20)


@pytest.fixture
async def host(st: Settings) -> AsyncIterator[VoiceHost]:
    h = VoiceHost(st, extra_env=DEBUG)
    try:
        yield h
    finally:
        await h.close()


def load(st: Settings, kind: str) -> Any:
    return Harness([Root("bundled", REPO)]).snapshot.backends[f"{kind}/sidecar"].load()(st)


async def test_round_trip_through_the_sidecar_backends(st: Settings) -> None:
    tts, stt, vad = load(st, "tts"), load(st, "stt"), load(st, "vad")
    try:
        assert (await stt.check()).ok
        audio = await tts.synthesize("hello from the sidecar", voice=None, speed=1.0)
        assert audio.media_type == "audio/wav" and audio.data[:4] == b"RIFF"
        t = await stt.transcribe(audio.data, language="de", keyterms=["oreo"])
        assert t.text == "hello from the sidecar" and t.language == "de" and t.segments
        assert await vad.speech_segments(audio.data)
        assert vad.is_speech(b"\x00\x00" * 480, 16_000) is False  # local energy test
        h = sidecar.get_host(st)
        assert h is sidecar.get_host(st) and h.starts == 1 and h.pid not in (None, os.getpid())
    finally:
        await stt.close()
        await tts.close()
        await vad.close()
    assert sidecar.get_host(st).pid is None  # close() shut the shared process down


async def test_crash_restarts_then_crash_loop_fails_honestly(
    host: VoiceHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sidecar, "BACKOFF_S", (0.0, 0.05))
    first = (await host.call("check", {}))["pid"]
    with pytest.raises(VoiceHostError, match="died during debug.crash"):
        await host.call("debug.crash", {})
    second = (await host.call("check", {}))["pid"]  # restarted lazily
    assert second != first and host.starts == 2 and host.recent_crashes() == 1
    for _ in range(sidecar.MAX_RESTARTS):
        with pytest.raises(VoiceHostError):
            await host.call("debug.crash", {})
    with pytest.raises(VoiceHostError, match="giving up"):
        await host.call("check", {})
    status = await host.check()
    assert not status.ok and "crashed 4 times" in status.detail
    host.crashes = []  # crashes aged out of the window → it starts again
    assert (await host.check()).ok


async def test_timeout_fails_the_request_but_keeps_the_process(host: VoiceHost) -> None:
    pid = (await host.call("check", {}))["pid"]
    with pytest.raises(VoiceHostError, match="did not answer debug.sleep"):
        await host.call("debug.sleep", {"s": 5}, timeout=0.3)
    assert (await host.call("check", {}))["pid"] == pid and host.starts == 1


async def test_payload_cap_both_sides(host: VoiceHost) -> None:
    with pytest.raises(ToolError, match="25 MB"):
        sidecar.encode(b"\x00" * (MAX_PAYLOAD_BYTES + 1))
    with pytest.raises(Exception, match="payload cap"):
        decode_payload("A" * (MAX_PAYLOAD_BYTES // 3 * 4 + 8))
    with pytest.raises(ToolError, match="not valid base64"):  # answered by the host, not a crash
        await host.call("transcribe", {"wav_b64": "!!!"})
    with pytest.raises(ToolError):  # inner backend error (not a WAV) → ToolError, host alive
        await host.call("transcribe", {"wav_b64": "aGVsbG8="})
    assert host.starts == 1 and host.pid is not None


async def test_env_is_scrubbed(host: VoiceHost, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-planted")
    monkeypatch.setenv("AUOREO_SERVER_PASSWORD", "planted")
    keys = set((await host.call("debug.env", {}))["keys"])
    assert "OPENROUTER_API_KEY" not in keys and "AUOREO_SERVER_PASSWORD" not in keys
    assert {"AUOREO_HOME", "AUOREO_VOICE_HOST_SETTINGS"} <= keys


async def test_declared_inner_secrets_only(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    keyed = home / "backends" / "stt" / "keyed"  # a runtime-root backend declaring one secret
    keyed.mkdir(parents=True)
    fake = (REPO / "backends" / "stt" / "fake" / "backend.yaml").read_text()
    (keyed / "backend.yaml").write_text(
        fake.replace("name: fake", "name: keyed").replace(
            "requires_env: []", "requires_env: [OPENAI_API_KEY]"
        )
    )
    shutil.copy(REPO / "backends" / "stt" / "fake" / "impl.py", keyed / "impl.py")
    st = Settings(home=home, voice_host_backends={"stt": "keyed"})
    assert "OPENAI_API_KEY" in sidecar.required_env(st)
    env = VoiceHost(st).env()
    assert env["OPENAI_API_KEY"] == "sk-test" and "GROQ_API_KEY" not in env


async def test_missing_interpreter_is_an_honest_check(st: Settings) -> None:
    h = VoiceHost(st, python="/nonexistent/python3")
    status = await h.check()
    assert not status.ok and "cannot start" in status.detail


async def test_inner_sidecar_recursion_is_refused(home: Path) -> None:
    h = VoiceHost(Settings(home=home, voice_host_backends={"stt": "sidecar"}))
    try:
        status = await h.check()
        assert not status.ok and "recurse" in status.detail
    finally:
        await h.close()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
async def test_api_transcribe_through_the_sidecar(home: Path, workdir: Path, gateway: FakeGateway) -> None:
    st = Settings(home=home, gateway="fake", approval_timeout_s=5, backends=dict(SIDECARS))
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        app = create_app(st, services=svc)
        async with (
            LifespanManager(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
        ):
            r = await c.post(
                "/api/voice/transcribe",
                files={"file": ("a.wav", tone_wav(1.0, comment="via sidecar"), "audio/wav")},
            )
            assert r.status_code == 200, r.text
            assert r.json()["text"] == "via sidecar" and r.json()["backend"] == "sidecar"
            assert sidecar.get_host(st).pid is not None
            rows = {b["kind"]: b for b in (await c.get("/api/voice/status")).json()["backends"]}
            assert rows["stt"]["ok"] and "voice-host pid" in rows["stt"]["detail"]
    finally:
        await svc.close()
    assert sidecar.get_host(st).pid is None  # Services.close → close_backends → sidecar shut down
