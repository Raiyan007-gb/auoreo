"""M4c audio helpers: ffmpeg normalize, WAV parsing, RMS, spoken-text shaping and chunking."""

from __future__ import annotations

import shutil

import pytest

from auoreo.voice import audio
from auoreo.voice.audio import AudioError, AudioTooLarge, VoiceUnavailable
from auoreo.voice.text import chunk_text, prepare_spoken_text
from tests.audio import silence_wav, tone_pcm, tone_wav

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@needs_ffmpeg
async def test_normalize_48k_stereo_to_16k_mono_keeps_comment() -> None:
    src = tone_wav(1.0, rate=48_000, channels=2, comment="hello there")
    out = await audio.normalize(src, "audio/wav")
    info = audio.read_wav(out)
    assert (info.sample_rate, info.channels, info.sample_width) == (16_000, 1, 2)
    assert abs(audio.duration_s(out) - 1.0) < 0.02
    assert info.comment == "hello there"
    assert out[4:8] != b"\xff\xff\xff\xff"  # clean RIFF size after the streamed ffmpeg output
    assert 0.3 < audio.rms(info.pcm) < 0.4  # 0.5-amplitude sine → RMS ≈ 0.354


@needs_ffmpeg
async def test_normalize_decodes_other_containers() -> None:
    ogg, _ = await audio.transcode(tone_wav(0.5, rate=48_000), "opus")
    assert ogg[:4] == b"OggS"
    out = await audio.normalize(ogg, "audio/ogg")
    assert audio.read_wav(out).sample_rate == 16_000
    assert abs(audio.duration_s(out) - 0.5) < 0.1


async def test_normalize_fast_path_without_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    already = tone_wav(0.2, comment="x")
    assert audio.read_wav(await audio.normalize(already, "audio/wav")).comment == "x"
    with pytest.raises(VoiceUnavailable, match="ffmpeg"):
        await audio.normalize(tone_wav(0.2, rate=48_000), "audio/wav")


async def test_normalize_caps_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audio, "MAX_INPUT_BYTES", 1000)
    with pytest.raises(AudioTooLarge, match="limit is 1000 bytes"):
        await audio.normalize(b"\0" * 1001, "audio/wav")
    with pytest.raises(AudioError, match="empty"):
        await audio.normalize(b"", "audio/wav")


@needs_ffmpeg
async def test_normalize_rejects_garbage() -> None:
    with pytest.raises(AudioError, match="could not decode"):
        await audio.normalize(b"definitely not audio" * 50, "audio/webm")


def test_read_wav_tolerates_streamed_sizes_and_rejects_non_wav() -> None:
    wav = bytearray(tone_wav(0.1))
    wav[4:8] = b"\xff\xff\xff\xff"
    data_at = wav.index(b"data")
    wav[data_at + 4 : data_at + 8] = b"\xff\xff\xff\xff"
    info = audio.read_wav(bytes(wav))
    assert len(info.pcm) == 1600 * 2
    with pytest.raises(AudioError, match="not a WAV"):
        audio.read_wav(b"ID3\x04garbage")


def test_rms_duration_and_concat() -> None:
    assert audio.rms(b"") == 0.0
    assert audio.rms(b"\0\0" * 100) == 0.0
    assert audio.rms(tone_pcm(0.1)) == pytest.approx(0.3535, abs=0.01)
    assert audio.duration_s(silence_wav(2.5)) == pytest.approx(2.5)
    joined = audio.concat_wavs([tone_wav(0.5, comment="a"), tone_wav(0.25, comment="b")])
    assert audio.duration_s(joined) == pytest.approx(0.75)
    assert audio.read_wav(joined).comment == "a b"
    with pytest.raises(AudioError, match="different formats"):
        audio.concat_wavs([tone_wav(0.1), tone_wav(0.1, rate=8000)])


def test_prepare_spoken_text_strips_markdown_emoji_and_code() -> None:
    md = (
        "# Results 🎉\n\n"
        "Here is **bold** and _italic_ text, see [the docs](https://example.com/x).\n"
        "- first item\n- second `item`\n"
        "```python\nprint('secret code')\n```\n"
        "> quoted e.g. this & that 👍\n"
        "| a | b |\n|---|---|\n"
    )
    out = prepare_spoken_text(md)
    for gone in ("#", "**", "_italic_", "https://", "`", "print(", "🎉", "👍", ">", "---", "[", "]"):
        assert gone not in out, (gone, out)
    assert "bold" in out and "italic" in out and "the docs" in out
    assert "for example" in out and "this and that" in out
    assert "code block omitted" in out
    assert prepare_spoken_text("🎉🎉") == ""


def test_chunk_text_respects_limit_and_sentences() -> None:
    text = "First sentence here. Second one is a bit longer! Third? " + "word " * 30
    chunks = chunk_text(text, 40)
    assert all(len(c) <= 40 for c in chunks)
    assert chunks[0] == "First sentence here."
    assert " ".join(chunks).split() == text.split()
    assert chunk_text("", 10) == []
    assert chunk_text("x" * 25, 10) == ["x" * 10, "x" * 10, "x" * 5]
    with pytest.raises(ValueError):
        chunk_text("a", 0)
