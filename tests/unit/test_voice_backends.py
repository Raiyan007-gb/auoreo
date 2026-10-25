"""M4c voice backends: fake round trips, honest `check()` of real backends, cloud request shapes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from auoreo.backends.stt import SttBackend
from auoreo.backends.tts import TtsBackend
from auoreo.backends.vad import VadBackend
from auoreo.backends.wake import WakeBackend
from auoreo.core.settings import Settings
from auoreo.harness.discovery import Root
from auoreo.harness.registry import Harness
from auoreo.tools.context import ToolError
from auoreo.voice import audio
from auoreo.voice.audio import VoiceUnavailable
from tests.audio import silence_wav, tone_pcm, tone_wav
from tests.conftest import REPO

KEYS = ("OPENAI_API_KEY", "GROQ_API_KEY", "ELEVENLABS_API_KEY")


@pytest.fixture
def harness() -> Harness:
    return Harness([Root("bundled", REPO)])


@pytest.fixture
def st(home: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    return Settings(home=home, gateway="fake")


def load(h: Harness, st: Settings, kind: str, name: str) -> Any:
    return h.snapshot.backends[f"{kind}/{name}"].load()(st)


# ------------------------------------------------------------------ manifests
def test_manifests_defaults_and_posture(harness: Harness) -> None:
    for kind, default in {"stt": "faster_whisper", "tts": "kittentts", "vad": "fake", "wake": "fake"}.items():
        assert harness.select_backend(kind, None).manifest.name == default
    assert harness.select_backend("desktop", None).manifest.name == "cua_driver"
    b = harness.snapshot.backends
    assert b["tts/piper"].manifest.posture == "gpl-isolated" and "GPL" in b["tts/piper"].manifest.license
    assert b["wake/openwakeword"].manifest.posture == "nc-opt-in"
    assert b["wake/sherpa_onnx"].manifest.posture == "ok"
    for key, d in b.items():
        if key.split("/")[0] in {"stt", "tts", "vad", "wake", "desktop"}:
            assert d.manifest.license, key


# ---------------------------------------------------------------------- fakes
async def test_fake_tts_stt_round_trip(harness: Harness, st: Settings) -> None:
    tts, stt = load(harness, st, "tts", "fake"), load(harness, st, "stt", "fake")
    assert isinstance(tts, TtsBackend) and isinstance(stt, SttBackend)
    text = "The quick brown fox jumps over the lazy dog."
    out = await tts.synthesize(text)
    assert out.media_type == "audio/wav"
    assert audio.duration_s(out.data) == pytest.approx(len(text) / 15, abs=0.01)
    t = await stt.transcribe(out.data, language="de", keyterms=["fox"])
    assert t.text == text and t.language == "de" and t.segments[0].text == text
    assert stt.calls[-1]["keyterms"] == ["fox"]
    plain = await stt.transcribe(tone_wav(2.0))
    assert plain.text == "fake transcript (2.0 s)"
    assert (await stt.check()).ok and (await tts.check()).ok


async def test_fake_vad_segments_and_frames(harness: Harness, st: Settings) -> None:
    vad = load(harness, st, "vad", "fake")
    assert isinstance(vad, VadBackend)
    wav = audio.wav_from_pcm(b"\0\0" * 8000 + tone_pcm(1.0) + b"\0\0" * 16000 + tone_pcm(0.5))
    segs = await vad.speech_segments(wav)
    assert len(segs) == 2
    assert segs[0][0] == pytest.approx(0.5, abs=0.04) and segs[0][1] == pytest.approx(1.5, abs=0.04)
    assert segs[1][0] == pytest.approx(2.5, abs=0.04)
    assert await vad.speech_segments(silence_wav(1.0)) == []
    assert vad.is_speech(tone_pcm(0.02), 16000) and not vad.is_speech(b"\0\0" * 320, 16000)


async def test_fake_wake(harness: Harness, st: Settings) -> None:
    wake = load(harness, st, "wake", "fake")
    assert isinstance(wake, WakeBackend) and wake.phrase == "hey oreo"
    hit = await wake.detect(tone_wav(0.5, comment="Hey  OREO, what's the weather"))
    assert hit is not None and hit.phrase == "hey oreo"
    assert await wake.detect(tone_wav(0.5, comment="hello there")) is None
    assert await wake.detect(tone_wav(0.5)) is None


# --------------------------------------------------- real backends, honestly
@pytest.mark.parametrize(
    ("kind", "name", "needle"),
    [
        ("stt", "faster_whisper", "faster-whisper"),
        ("tts", "kittentts", "kittentts"),
        ("vad", "silero", "silero"),
        ("wake", "sherpa_onnx", "sherpa-onnx"),
        ("wake", "openwakeword", "openwakeword"),
        ("tts", "piper", "piper"),
        ("stt", "openai", "OPENAI_API_KEY"),
        ("stt", "groq", "GROQ_API_KEY"),
        ("tts", "openai", "OPENAI_API_KEY"),
        ("tts", "elevenlabs", "ELEVENLABS_API_KEY"),
    ],
)
async def test_real_backends_report_not_ready_with_remedy(
    harness: Harness, st: Settings, kind: str, name: str, needle: str
) -> None:
    status = await load(harness, st, kind, name).check()
    assert status.ok is False, status
    assert needle in status.detail and status.remedy


async def test_stub_backends_refuse_to_run(harness: Harness, st: Settings) -> None:
    with pytest.raises(VoiceUnavailable, match="not implemented"):
        await load(harness, st, "tts", "piper").synthesize("hi")
    with pytest.raises(VoiceUnavailable, match="not implemented"):
        await load(harness, st, "wake", "openwakeword").detect(tone_wav(0.1))


async def test_missing_key_is_voice_unavailable(harness: Harness, st: Settings) -> None:
    with pytest.raises(VoiceUnavailable, match="OPENAI_API_KEY is not set"):
        await load(harness, st, "stt", "openai").transcribe(tone_wav(0.1))


# -------------------------------------------------------- cloud request shapes
def _form(req: httpx.Request) -> dict[str, list[str]]:
    """Text fields of a multipart body (the file part is skipped)."""
    out: dict[str, list[str]] = {}
    body = req.content
    boundary = req.headers["content-type"].split("boundary=")[1].encode()
    for part in body.split(b"--" + boundary):
        if b'name="' not in part or b"filename=" in part:
            continue
        head, _, value = part.partition(b"\r\n\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        out.setdefault(name, []).append(value.rstrip(b"\r\n").decode())
    return out


async def test_openai_stt_request_shape(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    httpx_mock.add_response(
        url="https://api.openai.com/v1/audio/transcriptions", json={"text": " hello world "}
    )
    t = await load(harness, st, "stt", "openai").transcribe(tone_wav(0.2), language="en", keyterms=["auoreo"])
    assert t.text == "hello world" and t.language == "en"
    req = httpx_mock.get_requests()[0]
    assert req.headers["authorization"] == "Bearer sk-test-openai"
    form = _form(req)
    assert form["model"] == ["gpt-4o-transcribe"] and form["response_format"] == ["json"]
    assert form["language"] == ["en"] and form["prompt"] == ["auoreo"]
    assert b'filename="audio.wav"' in req.content and b"RIFF" in req.content


async def test_openai_whisper1_asks_for_segments(
    harness: Harness, home: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    st = Settings(home=home, gateway="fake", voice_stt_model="whisper-1")
    httpx_mock.add_response(
        json={"text": "hi", "language": "english", "segments": [{"start": 0, "end": 0.4, "text": " hi"}]}
    )
    t = await load(harness, st, "stt", "openai").transcribe(tone_wav(0.2))
    assert t.segments[0].end == 0.4 and t.segments[0].text == "hi"
    form = _form(httpx_mock.get_requests()[0])
    assert form["model"] == ["whisper-1"] and form["response_format"] == ["verbose_json"]
    assert form["timestamp_granularities[]"] == ["segment"]


async def test_groq_stt_request_shape_and_error(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    httpx_mock.add_response(url=url, json={"text": "ok", "segments": []})
    backend = load(harness, st, "stt", "groq")
    assert (await backend.transcribe(tone_wav(0.2))).text == "ok"
    req = httpx_mock.get_requests()[0]
    assert req.headers["authorization"] == "Bearer gsk-test"
    form = _form(req)
    assert form["model"] == ["whisper-large-v3-turbo"] and form["response_format"] == ["verbose_json"]
    assert "language" not in form  # None fields are not sent
    httpx_mock.add_response(url=url, status_code=401, json={"error": {"message": "Invalid API Key"}})
    with pytest.raises(VoiceUnavailable, match="HTTP 401: Invalid API Key") as exc:
        await backend.transcribe(tone_wav(0.2))
    assert "gsk-test" not in str(exc.value)


@pytest.mark.parametrize("name", ["openai", "groq"])
async def test_cloud_stt_25mb_precheck_sends_nothing(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    big = b"RIFF" + b"\0" * (25 * 1024 * 1024)
    with pytest.raises(ToolError, match="25 MB"):
        await load(harness, st, "stt", name).transcribe(big)
    assert httpx_mock.get_requests() == []


async def test_openai_tts_request_shape(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-tts")
    httpx_mock.add_response(
        url="https://api.openai.com/v1/audio/speech",
        content=b"ID3mp3data",
        headers={"content-type": "audio/mpeg"},
    )
    tts = load(harness, st, "tts", "openai")
    assert tts.chunk_limit == 4096
    out = await tts.synthesize("Hello", speed=9.0)
    assert out.data == b"ID3mp3data" and out.media_type == "audio/mpeg"
    body = json.loads(httpx_mock.get_requests()[0].content)
    assert body == {
        "model": "gpt-4o-mini-tts",
        "input": "Hello",
        "voice": "marin",
        "response_format": "mp3",
        "speed": 4.0,
    }


async def test_elevenlabs_tts_request_shape(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-test")
    httpx_mock.add_response(
        url="https://api.elevenlabs.io/v1/text-to-speech/voice123?output_format=opus_48000_64",
        content=b"OggSopus",
    )
    out = await load(harness, st, "tts", "elevenlabs").synthesize("Hi there", voice="voice123", speed=1.1)
    assert out.media_type == "audio/ogg" and out.data == b"OggSopus"
    req = httpx_mock.get_requests()[0]
    assert req.headers["xi-api-key"] == "xi-test" and "authorization" not in req.headers
    body = json.loads(req.content)
    assert body == {"text": "Hi there", "model_id": "eleven_flash_v2_5", "voice_settings": {"speed": 1.1}}
