"""I8 voice: hallucination/echo filter, faster-whisper gates + CUDA fallback + idle unload,
keyterms, model auto-correct, local_command STT, Mistral/NeuTTS TTS, upload shaping, SILK,
WS Realtime fallback (fakes and contract tests only)."""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.core.settings_i8 import I8Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.harness.discovery import Root
from auoreo.harness.registry import Harness
from auoreo.voice import autocorrect, filters, keyterms, upload
from auoreo.voice.audio import AudioError, VoiceUnavailable, decode_silk, is_silk, normalize, read_wav
from tests.audio import silence_wav, tone_pcm, tone_wav
from tests.conftest import REPO

KEYS = ("MISTRAL_API_KEY", "OPENAI_API_KEY")


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


# ------------------------------------------------------------------ filter
def test_filter_blocklist_repeat_no_speech_and_echo() -> None:
    cfg = I8Settings().stt
    assert filters.verdict("Thank you for watching!", cfg).reason == "blocklist"
    assert filters.verdict("the the the the the", cfg).reason == "repeat"
    assert filters.verdict("go to sleep go to sleep go to sleep go to sleep", cfg).reason == "repeat"
    assert filters.verdict("open the build log", cfg, no_speech=True).reason == "no_speech"
    assert filters.verdict("  ...  ", cfg).reason == "empty"
    echo = filters.EchoMemory(window_s=20)
    echo.add("s1", "Sure, I opened the build log and the tests are green.", now=100.0)
    dropped = filters.verdict(
        "I opened the build log and the tests are green", cfg, echo=echo, session_id="s1", now=101.0
    )
    assert not dropped.keep and dropped.reason == "echo" and dropped.score >= 0.6
    kept = filters.verdict("please deploy to staging now", cfg, echo=echo, session_id="s1", now=101.0)
    assert kept.keep
    late = filters.verdict(
        "I opened the build log and the tests are green", cfg, echo=echo, session_id="s1", now=200.0
    )
    assert late.keep  # outside the sliding window
    off = cfg.model_copy(update={"filter_enabled": False})
    assert filters.verdict("thank you for watching", off).keep


def test_gate_segment_needs_both_conditions() -> None:
    cfg = I8Settings().stt
    assert filters.gate_segment(0.9, -0.2, cfg)  # confident text despite high no_speech
    assert filters.gate_segment(0.1, -2.0, cfg)  # low logprob but speech
    assert not filters.gate_segment(0.9, -2.0, cfg)  # both → drop
    assert filters.gate_segment(None, None, cfg)


# ---------------------------------------------------------- faster-whisper
def _fake_whisper(monkeypatch: pytest.MonkeyPatch, *, fail_cuda: bool, segs: list[Any]) -> dict[str, Any]:
    seen: dict[str, Any] = {"loads": []}

    class WhisperModel:
        def __init__(self, name: str, device: str, compute_type: str, local_files_only: bool) -> None:
            seen["loads"].append((device, compute_type))
            if device == "cuda" and fail_cuda:
                raise RuntimeError("CUDA driver missing")

        def transcribe(self, _audio: Any, **kw: Any) -> Any:
            seen["kw"] = kw
            return iter(segs), types.SimpleNamespace(language="en", language_probability=0.9)

    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = WhisperModel  # type: ignore[attr-defined]
    ct = types.ModuleType("ctranslate2")
    ct.get_cuda_device_count = lambda: 1  # type: ignore[attr-defined]
    ct.get_supported_compute_types = lambda device: {"float16", "int8"} if device == "cuda" else {"int8"}  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    monkeypatch.setitem(sys.modules, "ctranslate2", ct)
    monkeypatch.setattr(sys, "platform", "linux")
    return seen


def _seg(text: str, nsp: float, lp: float) -> Any:
    return types.SimpleNamespace(start=0.0, end=1.0, text=text, no_speech_prob=nsp, avg_logprob=lp)


async def test_faster_whisper_gates_cuda_fallback_and_idle_unload(
    harness: Harness, st: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    segs = [_seg(" hello there", 0.1, -0.3), _seg(" thanks for watching", 0.95, -1.6)]
    seen = _fake_whisper(monkeypatch, fail_cuda=True, segs=segs)
    stt = load(harness, st, "stt", "faster_whisper")
    mod = sys.modules[type(stt).__module__]
    mod.unload_all()
    t = await stt.transcribe(tone_wav(0.5), keyterms=["auoreo"])
    assert t.text == "hello there" and not t.no_speech
    assert seen["loads"] == [("cuda", "float16"), ("cpu", "int8")]  # CUDA failed → CPU int8
    assert seen["kw"]["no_speech_threshold"] == 0.6 and seen["kw"]["log_prob_threshold"] == -1.0
    assert seen["kw"]["initial_prompt"] == "auoreo"
    # every segment gated away → no_speech (re-listen)
    seen2 = _fake_whisper(monkeypatch, fail_cuda=False, segs=[_seg(" you", 0.9, -2.0)])
    mod.unload_all()
    t2 = await stt.transcribe(tone_wav(0.5))
    assert t2.no_speech and t2.text == "" and seen2["loads"] == [("cuda", "float16")]
    # idle unload
    st.i8.stt.unload_after_idle_s = 0.2
    mod.unload_all()
    await stt.transcribe(tone_wav(0.5))
    assert mod._models
    await asyncio.sleep(0.5)
    assert not mod._models
    assert mod.unload_all() == 0


# ------------------------------------------------------------------ keyterms
def test_default_keyterms_from_git_and_recent_files(tmp_path: Path) -> None:
    proj = tmp_path / "rocket-app"
    (proj / ".git").mkdir(parents=True)
    (proj / ".git" / "HEAD").write_text("ref: refs/heads/feature/voice-wake\n")
    (proj / "src").mkdir()
    for i, name in enumerate(["launch_pad.py", "orbit-calc.ts", "README.md"]):
        p = proj / "src" / name
        p.write_text("x")
        t = time.time() - 100 + i
        import os

        os.utime(p, (t, t))
    (proj / "node_modules").mkdir()
    (proj / "node_modules" / "skipme.js").write_text("x")
    keyterms._cache.clear()
    terms = keyterms.default_keyterms(proj, 50)
    assert terms[0] == "rocket-app" and "feature" in terms and "voice" in terms and "wake" in terms
    assert "README" in terms and "orbit-calc" in terms and "launch_pad" in terms
    assert "skipme" not in terms
    keyterms._cache.clear()
    assert len(keyterms.default_keyterms(proj, 2)) == 2
    wt = tmp_path / "wt"
    wt.mkdir()
    gitdir = tmp_path / "gitdir"
    gitdir.mkdir()
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
    (wt / ".git").write_text(f"gitdir: {gitdir}\n")
    assert keyterms.git_branch(wt) == "main"


async def test_voice_service_keyterms_autocorrect_and_filter(
    home: Path, workdir: Path, gateway: FakeGateway
) -> None:
    st = Settings(
        home=home, gateway="fake", backends={"stt": "fake", "vad": "fake"}, voice_stt_model="whisper-1"
    )
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        voice = svc.ext["voice"]
        (workdir / "deploy_script.sh").write_text("x")
        keyterms._cache.clear()
        await voice.transcribe(tone_wav(1.0, comment="ship it"), "audio/wav")
        assert "deploy_script" in svc.backend("stt").calls[-1]["keyterms"]
        assert autocorrect.correct("groq", "whisper-1") is not None
        assert autocorrect.correct("groq", "whisper-large-v3") is None
        assert autocorrect.correct("openai", "whisper-large-v3-turbo").corrected == "gpt-4o-transcribe"  # type: ignore[union-attr]
        assert autocorrect.correct("fake", "anything") is None
        # the hallucination filter turns a blocklisted push-to-talk transcript into "no speech"
        from auoreo.voice.audio import NoSpeech

        with pytest.raises(NoSpeech, match="blocklist"):
            await voice.transcribe(tone_wav(1.0, comment="Thanks for watching."), "audio/wav")
    finally:
        await svc.close()


async def test_model_correction_is_audited_once(home: Path, workdir: Path, gateway: FakeGateway) -> None:
    from sqlalchemy import select

    from auoreo.core.db import AuditRow

    st = Settings(
        home=home, gateway="fake", backends={"stt": "local_command"}, voice_stt_model="gpt-4o-transcribe"
    )
    svc = await Services.create(st, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        voice = svc.ext["voice"]
        autocorrect.LANES["local_command"] = (frozenset({"base"}), "base")
        try:
            await voice.check_model()
            await voice.check_model()
        finally:
            autocorrect.LANES["local_command"] = (None, "base")
        async with svc.db.session() as s:
            rows = [
                r
                for r in (await s.execute(select(AuditRow))).scalars()
                if r.action == "voice.stt_model_corrected"
            ]
        assert len(rows) == 1 and rows[0].detail["corrected"] == "base"
    finally:
        await svc.close()


# ------------------------------------------------------------- local_command
async def test_local_command_allowlist_and_run(harness: Harness, st: Settings, tmp_path: Path) -> None:
    stt = load(harness, st, "stt", "local_command")
    assert not (await stt.check()).ok
    script = tmp_path / "fake_stt.py"
    script.write_text(
        "import os, sys\n"
        "assert os.path.getsize(sys.argv[1]) > 44\n"
        "assert not any(k.endswith('_API_KEY') for k in os.environ)\n"
        "print('transcribed with', sys.argv[2])\n"
    )
    st.i8.stt.local_command.template = [sys.executable, str(script), "{input}", "{model}"]
    st.i8.stt.local_command.model = "tiny"
    with pytest.raises(VoiceUnavailable, match="allowlist"):
        await stt.transcribe(tone_wav(0.3))
    st.i8.stt.local_command.allowlist = [Path(sys.executable).name]
    assert (await stt.check()).ok
    t = await stt.transcribe(tone_wav(0.3))
    assert t.text == "transcribed with tiny"
    assert stt.last_run["exit_code"] == 0 and len(stt.last_run["cmd_sha"]) == 16
    st.i8.stt.local_command.template = [sys.executable, "-c", "import sys; sys.exit(3)"]
    with pytest.raises(AudioError, match="exited 3"):
        await stt.transcribe(tone_wav(0.3))


# ---------------------------------------------------------------------- TTS
async def test_mistral_tts_contract_and_moderation(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    tts = load(harness, st, "tts", "mistral")
    assert not (await tts.check()).ok
    monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")
    assert not (await tts.check()).ok  # no voice id
    st.i8.tts.mistral_voice = "v-123"
    assert (await tts.check()).ok and tts.chunk_limit == 4000
    url = "https://api.mistral.ai/v1/audio/speech"
    httpx_mock.add_response(url=url, json={"audio_data": base64.b64encode(b"ID3fake").decode()})
    audio = await tts.synthesize("hello " * 1000)
    assert audio.data == b"ID3fake" and audio.media_type == "audio/mpeg"
    req = httpx_mock.get_requests()[0]
    body = json.loads(req.content)
    assert (
        body["model"] == "voxtral-mini-tts-2603"
        and body["voice_id"] == "v-123"
        and len(body["input"]) == 4000
    )
    assert req.headers["authorization"] == "Bearer mk-test"
    httpx_mock.add_response(url=url, status_code=403, json={"message": "blocked"})
    with pytest.raises(VoiceUnavailable, match="moderation_403"):
        await tts.synthesize("x")


async def test_neutts_licence_gate_and_honest_check(harness: Harness, st: Settings, tmp_path: Path) -> None:
    tts = load(harness, st, "tts", "neutts")
    st.i8.tts.neutts_model = "neuphonic/neutts-nano"
    status = await tts.check()
    assert not status.ok and "Open License" in status.detail
    with pytest.raises(VoiceUnavailable, match="non-commercial"):
        await tts.synthesize("hi")
    st.i8.tts.neutts_model = "neuphonic/neutts-air"
    status = await tts.check()
    assert not status.ok and ("not installed" in status.detail or "reference" in status.detail)


async def test_neutts_with_fake_library(
    harness: Harness, st: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.machinery

    import numpy as np

    class NeuTTSAir:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

        def encode_reference(self, path: str) -> str:
            return f"codes:{path}"

        def infer(self, text: str, codes: str, ref_text: str) -> Any:
            assert codes.startswith("codes:") and ref_text == "hello ref"
            return np.full(2400, 0.25, dtype="float32")

    pkg = types.ModuleType("neuttsair")
    pkg.__spec__ = importlib.machinery.ModuleSpec("neuttsair", None)
    sub = types.ModuleType("neuttsair.neutts")
    sub.NeuTTSAir = NeuTTSAir  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "neuttsair", pkg)
    monkeypatch.setitem(sys.modules, "neuttsair.neutts", sub)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(tone_wav(0.2))
    st.i8.tts.neutts_ref_audio, st.i8.tts.neutts_ref_text = str(ref), "hello ref"
    tts = load(harness, st, "tts", "neutts")
    assert (await tts.check()).ok
    audio = await tts.synthesize("hi there")
    info = read_wav(audio.data)
    assert audio.media_type == "audio/wav" and info.sample_rate == 24_000 and len(info.pcm) == 4800


# ---------------------------------------------------------- upload shaping
async def test_trim_silence_and_aac_upload(st: Settings) -> None:
    from auoreo.voice.audio import wav_from_pcm

    wav = wav_from_pcm(b"\0\0" * 16000 + tone_pcm(0.5) + b"\0\0" * 16000)
    trimmed = upload.trim_silence(wav)
    dur = len(read_wav(trimmed).pcm) / 32000
    assert 0.85 <= dur <= 0.95  # 0.5 s + 2 × 200 ms pad
    assert upload.trim_silence(silence_wav(1.0)) == silence_wav(1.0)
    name, data, mime = await upload.prepare_upload(wav, st)
    assert (name, mime) == ("audio.wav", "audio/wav") and data == trimmed
    if shutil.which("ffmpeg"):
        st.i8.stt.cloud_aac = True
        name, data, mime = await upload.prepare_upload(wav, st)
        assert (name, mime) == ("audio.m4a", "audio/mp4") and data[4:8] == b"ftyp"
    assert await upload.prepare_upload(wav, None) == ("audio.wav", wav, "audio/wav")


async def test_cloud_upload_is_trimmed_for_groq(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from auoreo.voice.audio import wav_from_pcm

    monkeypatch.setenv("GROQ_API_KEY", "gk")
    httpx_mock.add_response(url="https://api.groq.com/openai/v1/audio/transcriptions", json={"text": "ok"})
    wav = wav_from_pcm(b"\0\0" * 32000 + tone_pcm(0.3))
    await load(harness, st, "stt", "groq").transcribe(wav)
    sent = httpx_mock.get_requests()[0].content
    assert len(sent) < len(wav) - 40000  # most of the 2 s of leading silence was cut


# ---------------------------------------------------------------- SILK
async def test_silk_honest_error_and_fake_decoder(monkeypatch: pytest.MonkeyPatch) -> None:
    silk = b"\x02#!SILK_V3" + b"\x00" * 64
    assert is_silk(silk) and is_silk(b"#!SILK_V3abc") and not is_silk(b"RIFF")
    monkeypatch.setitem(sys.modules, "pilk", None)
    monkeypatch.setitem(sys.modules, "pysilk", None)
    with pytest.raises(AudioError, match="pilk"):
        decode_silk(silk)

    def fake_decode(src: str, dst: str, pcm_rate: int) -> float:
        assert Path(src).read_bytes() == silk and pcm_rate == 24_000
        Path(dst).write_bytes(tone_pcm(0.5, rate=24_000))
        return 0.5

    pilk = types.ModuleType("pilk")
    pilk.decode = fake_decode  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pilk", pilk)
    info = read_wav(decode_silk(silk))
    assert info.sample_rate == 24_000 and len(info.pcm) == 24_000
    if shutil.which("ffmpeg"):
        out = read_wav(await normalize(silk, "audio/silk"))
        assert out.sample_rate == 16_000 and abs(len(out.pcm) - 16_000) < 400


# ------------------------------------------------------- realtime fallback
async def test_realtime_join_offers_websocket_fallback(
    harness: Harness, st: Settings, httpx_mock: HTTPXMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    httpx_mock.add_response(url="https://api.openai.com/v1/realtime/client_secrets", json={"value": "ek_1"})
    rt = load(harness, st, "rtc_transport", "openai_realtime")
    info = await rt.join(session_id="ses_1", identity="u", offer_sdp=None)
    fb = info.extra["fallback"]
    assert (
        fb["transport"] == "websocket" and fb["url"] == "wss://api.openai.com/v1/realtime?model=gpt-realtime"
    )
    assert fb["protocols"] == ["realtime", "openai-insecure-api-key.ek_1"] and fb["chain"][0] == "webrtc"
    st.i8.realtime_ws.enabled = False
    httpx_mock.add_response(url="https://api.openai.com/v1/realtime/client_secrets", json={"value": "ek_2"})
    info = await rt.join(session_id="ses_1", identity="u", offer_sdp=None)
    assert "fallback" not in info.extra


def test_i8_settings_from_env(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    monkeypatch.setenv(
        "AUOREO_I8", json.dumps({"wake": {"enabled": True, "routes": {"hey oreo": {"agent": "build"}}}})
    )
    st = Settings(home=home)
    assert st.i8.wake.enabled and st.i8.wake.routes["hey oreo"].agent == "build"
    assert st.i8.computer_use.max_image_dimension == 1456 and st.i8.stt.echo_similarity == 0.6
