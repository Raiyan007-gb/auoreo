"""G6 voice lanes: extra cloud STT request shapes, OpenAI streaming deltas, Deepgram over a fake
WebSocket server, OpenAI Realtime ephemeral mint + SDP exchange, porcupine with a fake engine,
edge-tts as a scrubbed subprocess, and the sherpa-face stub's honest check."""

from __future__ import annotations

import importlib.machinery
import json
import sys
import types
from pathlib import Path
from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from auoreo.backends.stt import TranscriptDelta
from auoreo.core.settings import Settings
from auoreo.harness.discovery import Root
from auoreo.harness.registry import Harness
from auoreo.tools.context import ToolError
from auoreo.voice.audio import VoiceUnavailable, wav_from_pcm
from tests.audio import silence_wav, tone_pcm, tone_wav
from tests.conftest import REPO

KEYS = ("MISTRAL_API_KEY", "XAI_API_KEY", "ELEVENLABS_API_KEY", "DEEPINFRA_API_KEY", "DEEPGRAM_API_KEY")


@pytest.fixture
def harness() -> Harness:
    return Harness([Root("bundled", REPO)])


@pytest.fixture
def st(home: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for k in (*KEYS, "PICOVOICE_ACCESS_KEY", "PORCUPINE_ACCESS_KEY"):
        monkeypatch.delenv(k, raising=False)
    return Settings(home=home, gateway="fake")


def load(h: Harness, st: Settings, kind: str, name: str) -> Any:
    return h.snapshot.backends[f"{kind}/{name}"].load()(st)


def _form(req: httpx.Request) -> list[tuple[str, str]]:
    """Ordered (name, value) text parts of a multipart body (the file part excluded)."""
    ctype = req.headers["content-type"]
    boundary = ctype.split("boundary=")[1].encode()
    out: list[tuple[str, str]] = []
    for part in req.content.split(b"--" + boundary):
        if b'name="' not in part or b'filename="' in part:
            continue
        head, _, body = part.partition(b"\r\n\r\n")
        name = head.split(b'name="')[1].split(b'"')[0].decode()
        out.append((name, body.rstrip(b"\r\n").decode()))
    return out


# ------------------------------------------------------------------ manifests
def test_g6_manifests_and_posture(harness: Harness) -> None:
    b = harness.snapshot.backends
    for key in (
        "stt/mistral",
        "stt/xai",
        "stt/elevenlabs",
        "stt/deepinfra",
        "stt/deepgram",
        "rtc_transport/openai_realtime",
        "wake/porcupine",
        "tts/edge_tts",
        "face/sherpa_onnx",
    ):
        assert key in b, key
        assert not b[key].manifest.default, key
    assert b["tts/edge_tts"].manifest.posture == "gpl-isolated"
    assert b["wake/porcupine"].manifest.requires_env[0] == "PICOVOICE_ACCESS_KEY"
    assert b["stt/deepgram"].manifest.requires_env == ["DEEPGRAM_API_KEY"]
    # defaults are unchanged by G6
    assert harness.select_backend("stt", None).manifest.name == "faster_whisper"
    assert harness.select_backend("wake", None).manifest.name == "fake"


@pytest.mark.parametrize("name", ["mistral", "elevenlabs", "deepinfra", "deepgram"])
async def test_cloud_stt_without_key_is_honest(harness: Harness, st: Settings, name: str) -> None:
    be = load(harness, st, "stt", name)
    status = await be.check()
    assert not status.ok and "auoreo secrets set" in status.remedy
    with pytest.raises(VoiceUnavailable):
        await be.transcribe(tone_wav(0.5))


# ----------------------------------------------------------------- cloud STT
async def test_mistral_request_shape(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")
    url = "https://api.mistral.ai/v1/audio/transcriptions"
    httpx_mock.add_response(
        url=url, json={"text": " hello ", "segments": [{"start": 0, "end": 1.5, "text": "hello"}]}
    )
    t = await load(harness, st, "stt", "mistral").transcribe(
        tone_wav(1.0), language="de", keyterms=["AUOREO", "Hey Oreo"]
    )
    assert t.text == "hello" and t.segments[0].end == 1.5
    req = httpx_mock.get_requests()[0]
    assert req.headers["x-api-key"] == "mk-test" and "authorization" not in req.headers
    form = _form(req)
    assert ("model", "voxtral-mini-latest") in form and ("timestamp_granularities", "segment") in form
    assert [v for k, v in form if k == "context_bias"] == ["AUOREO", "Hey_Oreo"]
    assert not any(k == "language" for k, _ in form)  # never together with timestamps


async def test_elevenlabs_stt_request_shape(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-test")
    words = [
        {"text": "Hi", "start": 0.0, "end": 0.2, "type": "word"},
        {"text": " ", "start": 0.2, "end": 0.25, "type": "spacing"},
        {"text": "there.", "start": 0.3, "end": 0.6, "type": "word"},
        {"text": "Bye", "start": 1.0, "end": 1.2, "type": "word"},
    ]
    httpx_mock.add_response(
        url="https://api.elevenlabs.io/v1/speech-to-text",
        json={"text": "Hi there. Bye", "language_code": "en", "language_probability": 0.9, "words": words},
    )
    t = await load(harness, st, "stt", "elevenlabs").transcribe(
        tone_wav(1.0), language="en", keyterms=["Oreo"]
    )
    assert t.text == "Hi there. Bye" and t.language == "en" and t.confidence == 0.9
    assert [(s.text, s.start, s.end) for s in t.segments] == [("Hi there.", 0.0, 0.6), ("Bye", 1.0, 1.2)]
    req = httpx_mock.get_requests()[0]
    assert req.headers["xi-api-key"] == "xi-test"
    form = dict(_form(req))
    assert form["model_id"] == "scribe_v2" and form["language_code"] == "en" and form["keyterms"] == "Oreo"


async def test_deepinfra_request_shape_and_error(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPINFRA_API_KEY", "di-test")
    url = "https://api.deepinfra.com/v1/openai/audio/transcriptions"
    httpx_mock.add_response(url=url, json={"text": "ok", "segments": []})
    be = load(harness, st, "stt", "deepinfra")
    assert (await be.transcribe(tone_wav(0.5))).text == "ok"
    req = httpx_mock.get_requests()[0]
    assert req.headers["authorization"] == "Bearer di-test"
    assert dict(_form(req))["model"] == "openai/whisper-large-v3-turbo"
    httpx_mock.add_response(url=url, status_code=401, json={"error": {"message": "bad key"}})
    with pytest.raises(VoiceUnavailable) as exc:
        await be.transcribe(tone_wav(0.5))
    assert "401" in str(exc.value) and "di-test" not in str(exc.value)


async def test_xai_is_gated_then_uses_configured_url(
    harness: Harness, home: Path, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XAI_API_KEY", "xai-test")
    st = Settings(home=home, gateway="fake")
    be = load(harness, st, "stt", "xai")
    status = await be.check()
    assert not status.ok and "xai_experimental" in status.remedy
    with pytest.raises(VoiceUnavailable):
        await be.transcribe(tone_wav(0.5))
    assert httpx_mock.get_requests() == []
    st2 = Settings(
        home=home,
        gateway="fake",
        g6={"voice": {"xai_experimental": True, "xai_stt_url": "https://x.test/stt"}},
    )
    be2 = load(harness, st2, "stt", "xai")
    assert (await be2.check()).ok
    httpx_mock.add_response(url="https://x.test/stt", json={"text": "grok"})
    assert (await be2.transcribe(tone_wav(0.5), language="en")).text == "grok"


async def test_upload_cap_sends_nothing(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    with pytest.raises(ToolError):
        await load(harness, st, "stt", "mistral").transcribe(b"\0" * (25 * 1024 * 1024 + 1))
    assert httpx_mock.get_requests() == []


# ---------------------------------------------------------- streaming deltas
async def test_openai_stream_deltas(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    events = [
        {"type": "transcript.text.delta", "delta": "Hello"},
        {"type": "transcript.text.delta", "delta": " world"},
        {"type": "transcript.text.done", "text": "Hello world", "languages": [{"code": "en"}]},
    ]
    sse = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    httpx_mock.add_response(
        url="https://api.openai.com/v1/audio/transcriptions",
        content=sse.encode(),
        headers={"content-type": "text/event-stream"},
    )
    be = load(harness, st, "stt", "openai")
    assert be.streaming
    out = [d async for d in be.stream(tone_wav(1.0), keyterms=["Oreo"])]
    assert out == [
        TranscriptDelta("Hello"),
        TranscriptDelta(" world"),
        TranscriptDelta("Hello world", final=True, done=True, language="en"),
    ]
    form = dict(_form(httpx_mock.get_requests()[0]))
    assert form["stream"] == "true" and form["model"] == "gpt-4o-transcribe" and form["prompt"] == "Oreo"


async def test_default_stream_wraps_transcribe(harness: Harness, st: Settings) -> None:
    fake = load(harness, st, "stt", "fake")
    assert not fake.streaming
    out = [d async for d in fake.stream(tone_wav(2.0))]
    assert len(out) == 1 and out[0].done and out[0].text == "fake transcript (2.0 s)"


async def test_deepgram_against_fake_websocket_server(
    harness: Harness, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from websockets.asyncio.server import serve

    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    seen: dict[str, Any] = {"bytes": 0, "conns": 0}

    async def handler(ws: Any) -> None:
        seen["conns"] += 1
        seen.setdefault("path", ws.request.path)
        seen["auth"] = ws.request.headers.get("Authorization")
        async for msg in ws:
            if isinstance(msg, bytes):
                seen["bytes"] += len(msg) if seen["conns"] == 1 else 0
                continue
            if json.loads(msg).get("type") == "CloseStream":
                break
        for text, final in (
            ("hel", False),
            ("hello there", True),
            ("general", False),
            ("general kenobi", True),
        ):
            await ws.send(
                json.dumps(
                    {
                        "type": "Results",
                        "is_final": final,
                        "channel": {"alternatives": [{"transcript": text}]},
                    }
                )
            )
        await ws.send(json.dumps({"type": "Metadata", "duration": 1.0}))
        await ws.close()

    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        st = Settings(
            home=home, gateway="fake", g6={"voice": {"deepgram_url": f"ws://127.0.0.1:{port}/v1/listen"}}
        )
        be = load(harness, st, "stt", "deepgram")
        assert (await be.check()).ok and be.streaming
        wav = wav_from_pcm(tone_pcm(1.0))
        deltas = [d async for d in be.stream(wav, language="en", keyterms=["kenobi"])]
        assert [d.text for d in deltas if not d.done] == ["hel", "hello there", "general", "general kenobi"]
        assert deltas[-1] == TranscriptDelta(
            "hello there general kenobi", final=True, done=True, language="en"
        )
        t = await be.transcribe(wav)
        assert t.text == "hello there general kenobi" and t.segments[0].end == pytest.approx(1.0)
    assert seen["auth"] == "Token dg-test"
    assert "encoding=linear16" in seen["path"] and "sample_rate=16000" in seen["path"]
    assert "model=nova-3" in seen["path"] and "keyterm=kenobi" in seen["path"]
    assert seen["bytes"] == 32000  # 1 s of 16 kHz int16, sent in 250 ms frames


async def test_deepgram_unreachable_is_voice_unavailable(
    harness: Harness, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test")
    st = Settings(home=home, gateway="fake", g6={"voice": {"deepgram_url": "ws://127.0.0.1:9/v1/listen"}})
    with pytest.raises(VoiceUnavailable) as exc:
        await load(harness, st, "stt", "deepgram").transcribe(tone_wav(0.3))
    assert "dg-test" not in str(exc.value)


# ------------------------------------------------------------ OpenAI Realtime
SDP_OFFER = "v=0\r\no=- 1 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"
SDP_ANSWER = "v=0\r\no=- 9 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"


async def test_realtime_mint_and_sdp_exchange(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-std")
    rt = load(harness, st, "rtc_transport", "openai_realtime")
    assert (await rt.check()).ok
    httpx_mock.add_response(
        url="https://api.openai.com/v1/realtime/client_secrets",
        json={"value": "ek_123", "expires_at": 1999999999, "session": {"type": "realtime"}},
    )
    httpx_mock.add_response(
        url="https://api.openai.com/v1/realtime/calls",
        text=SDP_ANSWER,
        headers={"location": "/v1/realtime/calls/rtc_abc"},
    )
    info = await rt.join(session_id="ses_1", identity="user", offer_sdp=SDP_OFFER)
    assert info.transport == "openai_realtime" and info.sdp == SDP_ANSWER and info.token is None
    assert info.extra["call_id"] == "rtc_abc" and info.extra["expires_at"] == 1999999999
    mint, call = httpx_mock.get_requests()
    assert mint.headers["authorization"] == "Bearer sk-std"
    assert (
        len(mint.headers["openai-safety-identifier"]) == 32
        and "ses_1" not in mint.headers["openai-safety-identifier"]
    )
    body = json.loads(mint.content)
    assert body["session"]["type"] == "realtime" and body["session"]["model"] == "gpt-realtime"
    assert call.headers["authorization"] == "Bearer ek_123"  # the ephemeral secret, not the key
    assert call.headers["content-type"] == "application/sdp" and call.content.decode() == SDP_OFFER
    assert rt.mints == [
        {
            "session_id": "ses_1",
            "minted_at": rt.mints[0]["minted_at"],
            "model": "gpt-realtime",
            "outcome": "ok",
        }
    ]
    assert "ek_123" not in json.dumps(await rt.describe())
    httpx_mock.add_response(url="https://api.openai.com/v1/realtime/calls/rtc_abc/hangup", status_code=200)
    await rt.leave(info.extra["join_id"])
    assert httpx_mock.get_requests()[-1].headers["authorization"] == "Bearer sk-std"
    await rt.leave(info.extra["join_id"])  # idempotent


async def test_realtime_without_offer_hands_out_ephemeral(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-std")
    httpx_mock.add_response(
        url="https://api.openai.com/v1/realtime/client_secrets",
        json={"client_secret": {"value": "ek_legacy", "expires_at": 5}},
    )
    info = await load(harness, st, "rtc_transport", "openai_realtime").join(
        session_id=None, identity="user", offer_sdp=None
    )
    assert info.token == "ek_legacy" and info.url == "https://api.openai.com/v1/realtime/calls"


async def test_realtime_failures(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt = load(harness, st, "rtc_transport", "openai_realtime")
    assert not (await rt.check()).ok
    with pytest.raises(RuntimeError):
        await rt.join(session_id=None, identity="u", offer_sdp=SDP_OFFER)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-std")
    httpx_mock.add_response(url="https://api.openai.com/v1/realtime/client_secrets", status_code=401, json={})
    with pytest.raises(ConnectionError) as exc:
        await rt.join(session_id="s", identity="u", offer_sdp=SDP_OFFER)
    assert "401" in str(exc.value) and rt.mints[-1]["outcome"] == "http_401"
    httpx_mock.add_response(url="https://api.openai.com/v1/realtime/client_secrets", json={"value": "ek"})
    httpx_mock.add_response(url="https://api.openai.com/v1/realtime/calls", status_code=400, text="bad sdp")
    with pytest.raises(ConnectionError):
        await rt.join(session_id="s", identity="u", offer_sdp=SDP_OFFER)


# ------------------------------------------------------------------ porcupine
class _FakePorcupine:
    frame_length = 512
    sample_rate = 16000

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.deleted = False

    def process(self, frame: list[int]) -> int:
        return 0 if max(abs(x) for x in frame) > 3000 else -1

    def delete(self) -> None:
        self.deleted = True


@pytest.fixture
def fake_pvporcupine(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    made: dict[str, Any] = {}
    mod = types.ModuleType("pvporcupine")
    mod.__spec__ = importlib.machinery.ModuleSpec("pvporcupine", None)

    def create(**kwargs: Any) -> _FakePorcupine:
        made["engine"] = _FakePorcupine(**kwargs)
        return made["engine"]

    mod.create = create  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pvporcupine", mod)
    return made


async def test_porcupine_fails_closed_without_key(
    harness: Harness, st: Settings, fake_pvporcupine: dict[str, Any]
) -> None:
    be = load(harness, st, "wake", "porcupine")
    status = await be.check()
    assert not status.ok and "fail-closed" in status.detail and "PICOVOICE_ACCESS_KEY" in status.remedy
    with pytest.raises(VoiceUnavailable):
        await be.detect(tone_wav(0.5))
    assert "engine" not in fake_pvporcupine


async def test_porcupine_detects_with_fake_engine(
    harness: Harness, home: Path, fake_pvporcupine: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PORCUPINE_ACCESS_KEY", "pv-key")  # alias accepted
    st = Settings(
        home=home,
        gateway="fake",
        g6={"voice": {"porcupine_keywords": ["jarvis"], "porcupine_sensitivity": 0.7}},
    )
    be = load(harness, st, "wake", "porcupine")
    assert (await be.check()).ok
    assert await be.detect(silence_wav(1.0)) is None
    hit = await be.detect(wav_from_pcm(b"\0\0" * 16000 + tone_pcm(0.5)))
    assert hit is not None and hit.phrase == "jarvis" and hit.start == pytest.approx(1.0, abs=0.04)
    engine = fake_pvporcupine["engine"]
    assert engine.kwargs == {"access_key": "pv-key", "sensitivities": [0.7], "keywords": ["jarvis"]}
    await be.close()
    assert engine.deleted


async def test_porcupine_missing_library_and_keyword_file(
    harness: Harness, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(sys.modules, "pvporcupine", raising=False)
    monkeypatch.setattr("importlib.util.find_spec", lambda name, *a: None)
    st = Settings(home=home, gateway="fake")
    status = await load(harness, st, "wake", "porcupine").check()
    assert not status.ok and "wake-porcupine" in status.remedy


# ------------------------------------------------------------------- edge-tts
FAKE_EDGE = """
import os, sys
args = sys.argv[1:]
assert "OPENAI_API_KEY" not in os.environ, "provider key leaked into the edge-tts child"
out = args[args.index("--write-media") + 1]
voice = args[args.index("--voice") + 1]
rate = [a for a in args if a.startswith("--rate=")][0]
text = args[args.index("--text") + 1]
open(out, "wb").write(("ID3|" + voice + "|" + rate + "|" + text).encode())
"""


async def test_edge_tts_runs_out_of_process(
    harness: Harness, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = tmp_path / "fake_edge.py"
    script.write_text(FAKE_EDGE)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-must-not-leak")
    st = Settings(
        home=home, gateway="fake", g6={"voice": {"edge_tts_command": [sys.executable, str(script)]}}
    )
    be = load(harness, st, "tts", "edge_tts")
    assert (await be.check()).ok and be.chunk_limit == 5000
    audio = await be.synthesize("Hello there", speed=1.5)
    assert audio.media_type == "audio/mpeg"
    assert audio.data == b"ID3|en-US-EmmaMultilingualNeural|--rate=+50%|Hello there"
    slow = await be.synthesize("x", voice="de-DE-KatjaNeural", speed=0.5)
    assert b"de-DE-KatjaNeural|--rate=-50%" in slow.data
    assert "edge_tts" not in sys.modules  # never imported in-process


async def test_edge_tts_missing_and_failing(harness: Harness, home: Path, tmp_path: Path) -> None:
    st = Settings(home=home, gateway="fake", g6={"voice": {"edge_tts_command": ["/nonexistent/edge-tts"]}})
    be = load(harness, st, "tts", "edge_tts")
    assert not (await be.check()).ok
    with pytest.raises(VoiceUnavailable):
        await be.synthesize("hi")
    bad = tmp_path / "bad.py"
    bad.write_text("import sys; sys.stderr.write('boom: no network'); sys.exit(3)")
    st2 = Settings(home=home, gateway="fake", g6={"voice": {"edge_tts_command": [sys.executable, str(bad)]}})
    with pytest.raises(ToolError) as exc:
        await load(harness, st2, "tts", "edge_tts").synthesize("hi")
    assert "exit 3" in str(exc.value) and "boom" in str(exc.value)


# ---------------------------------------------------------------- sherpa face
async def test_sherpa_face_stub_is_honest(harness: Harness, st: Settings, tmp_path: Path) -> None:
    be = load(harness, st, "face", "sherpa_onnx")
    status = await be.check()
    assert not status.ok and "stub" in status.detail
    with pytest.raises(ToolError):
        await be.faces(b"\x89PNG")


def test_rate_arg_clamps() -> None:
    mod = Harness([Root("bundled", REPO)]).snapshot.backends["tts/edge_tts"].load()
    rate_arg = sys.modules[mod.__module__].rate_arg
    assert rate_arg(1.0) == "--rate=+0%" and rate_arg(10) == "--rate=+300%" and rate_arg(0.1) == "--rate=-75%"
