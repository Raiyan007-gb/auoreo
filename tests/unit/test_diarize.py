"""F4 diarization backends: deterministic fake, honest `check()` of the real ones."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from auoreo.backends.diarize import DiarizeBackend, Turn, relabel
from auoreo.core.settings import Settings
from auoreo.harness.discovery import Root
from auoreo.harness.registry import Harness
from auoreo.voice.audio import VoiceUnavailable, wav_from_pcm
from tests.audio import tone_pcm
from tests.conftest import REPO


@pytest.fixture
def harness() -> Harness:
    return Harness([Root("bundled", REPO)])


@pytest.fixture
def st(home: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("HF_TOKEN", raising=False)
    return Settings(home=home, gateway="fake")


def load(h: Harness, st: Settings, name: str) -> Any:
    inst = h.snapshot.backends[f"diarize/{name}"].load()(st)
    assert isinstance(inst, DiarizeBackend)
    return inst


def silence(seconds: float) -> bytes:
    return b"\0\0" * int(seconds * 16_000)


def conversation() -> bytes:
    """A (440 Hz) · B (1200 Hz, quieter) · A, separated by 0.6 s gaps."""
    a = tone_pcm(1.0, freq=440.0, amp=0.5)
    b = tone_pcm(1.2, freq=1200.0, amp=0.25)
    return wav_from_pcm(a + silence(0.6) + b + silence(0.6) + a)


def test_manifests(harness: Harness) -> None:
    assert harness.select_backend("diarize", None).manifest.name == "fake"  # type: ignore[union-attr]
    assert set(harness.backends_for("diarize")) == {"fake", "sherpa_onnx", "pyannote"}
    for name in ("sherpa_onnx", "pyannote"):
        assert harness.snapshot.backends[f"diarize/{name}"].manifest.posture == "ok"
    assert "HF_TOKEN" in harness.snapshot.backends["diarize/pyannote"].manifest.description


def test_relabel_orders_by_first_appearance() -> None:
    turns = relabel([(2.0, 3.0, 7), (0.0, 1.0, 3), (4.0, 5.0, 3)])
    assert [t.speaker for t in turns] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]
    assert turns[0] == Turn(0.0, 1.0, "SPEAKER_00")


async def test_fake_alternates_speakers_deterministically(harness: Harness, st: Settings) -> None:
    fake = load(harness, st, "fake")
    assert (await fake.check()).ok
    wav = conversation()
    first = await fake.diarize(wav)
    assert [t.speaker for t in first] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]
    assert first[0].start == pytest.approx(0.0, abs=0.05) and first[0].end == pytest.approx(1.0, abs=0.05)
    assert first[1].start == pytest.approx(1.6, abs=0.05) and first[1].end == pytest.approx(2.8, abs=0.05)
    assert await fake.diarize(wav) == first  # deterministic
    assert await load(harness, st, "fake").diarize(wav) == first


async def test_fake_short_gap_and_cap(harness: Harness, st: Settings) -> None:
    fake = load(harness, st, "fake")
    a = tone_pcm(0.5, freq=440.0)
    b = tone_pcm(0.5, freq=1200.0, amp=0.25)
    one = await fake.diarize(wav_from_pcm(a + silence(0.1) + a))  # gap < 0.3 s → one turn
    assert len(one) == 1 and one[0].speaker == "SPEAKER_00"
    capped = await fake.diarize(wav_from_pcm(a + silence(0.5) + b), num_speakers=1)
    assert {t.speaker for t in capped} == {"SPEAKER_00"} and len(capped) == 1
    assert await fake.diarize(wav_from_pcm(silence(1.0))) == []


async def test_sherpa_check_is_honest(
    harness: Harness, st: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    sd = load(harness, st, "sherpa_onnx")
    impl = sys.modules[type(sd).__module__]
    real = importlib.util.find_spec
    monkeypatch.setattr(impl.importlib.util, "find_spec", lambda n: None if n == "sherpa_onnx" else real(n))
    status = await sd.check()
    assert not status.ok and "sherpa-onnx" in status.remedy
    monkeypatch.setattr(impl.importlib.util, "find_spec", lambda n: object())
    status = await sd.check()
    assert not status.ok and "missing model" in status.detail and "voice_diarize" in status.remedy
    with pytest.raises(VoiceUnavailable, match="voice_diarize_segmentation_model"):
        sd._diarizer(None)
    st.voice_download_models = True
    assert "will download" in (await sd.check()).detail
    st.voice_download_models = False
    seg, emb = st.home / "seg.onnx", st.home / "emb.onnx"
    seg.write_bytes(b"x")
    emb.write_bytes(b"x")
    st.voice_diarize_segmentation_model, st.voice_diarize_embedding_model = seg, emb
    status = await sd.check()
    assert status.ok and "seg.onnx" in status.detail


async def test_pyannote_check_is_honest(
    harness: Harness, st: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    pa = load(harness, st, "pyannote")
    impl = sys.modules[type(pa).__module__]
    monkeypatch.setattr(impl.importlib.util, "find_spec", lambda n: None)
    status = await pa.check()
    assert not status.ok and "pyannote.audio" in status.remedy
    monkeypatch.setattr(impl.importlib.util, "find_spec", lambda n: object())
    status = await pa.check()
    assert not status.ok and "HF_TOKEN" in status.detail and "secrets set HF_TOKEN" in status.remedy
    with pytest.raises(VoiceUnavailable, match="HF_TOKEN"):
        pa._load()
    monkeypatch.setenv("HF_TOKEN", "hf_test_token_value")
    status = await pa.check()
    assert status.ok and "GPU recommended" in status.detail and "hf_test" not in status.detail
