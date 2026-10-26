"""I7 (spec §13.4 L615): the `MEDIA:` envelope → voice-note deliveries (Opus for chat apps, MP3
elsewhere, 16 MiB cap, sessions-folder confinement, honest ffmpeg errors), plus the connector
reply path (`InboundRouter`) with voice notes and `stream: true` partial replies."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from auoreo.automation.deliveries import voice_notes
from auoreo.core.services import Services
from auoreo.core.settings import write_settings_value
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.plugins.connectors import InboundRouter
from auoreo.voice import audio, notes
from tests.audio import tone_wav

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg binary not installed")


def wav_in_session(home: Path, name: str = "1.wav", seconds: float = 1.5) -> Path:
    path = home / "sessions" / "ses1" / "audio" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tone_wav(seconds))
    return path


class Svc:
    def __init__(self, home: Path) -> None:
        self.settings = type("S", (), {"home": home})()


def test_envelope_round_trip_and_extract() -> None:
    line = notes.envelope("/x/sessions/a/audio/1.wav", "audio/wav", 2.345)
    assert line.startswith("MEDIA:{") and json.loads(line[6:])["duration_s"] == 2.35
    text = f"Here you go.\n{line}\n\nMEDIA:/x/sessions/a/audio/2.mp3\nMEDIA: not-json{{"
    clean, refs = notes.extract(text)
    assert [r.path for r in refs] == ["/x/sessions/a/audio/1.wav", "/x/sessions/a/audio/2.mp3", "not-json{"]
    assert refs[1].mime == "audio/mpeg" and refs[0].duration_s == 2.35
    assert clean == "Here you go."
    many = "\n".join(f"MEDIA:/a/{i}.wav" for i in range(9))
    assert len(notes.extract(many)[1]) == notes.MAX_ITEMS
    assert notes.target_format("telegram") == "opus" and notes.target_format("email") == "mp3"


async def test_confinement_refuses_files_outside_sessions(home: Path) -> None:
    (home / "secrets.db").write_bytes(b"SQLite")
    wav_in_session(home)
    for path in (home / "secrets.db", home / "sessions" / "nope.wav", Path("/etc/passwd")):
        with pytest.raises(notes.MediaError):
            notes.confine(notes.MediaRef(str(path), "audio/wav"), home)
    txt = home / "sessions" / "ses1" / "audio" / "x.txt"
    txt.write_text("hi")
    with pytest.raises(notes.MediaError, match="not an audio file"):
        notes.confine(notes.MediaRef(str(txt), ""), home)
    body, extra = await voice_notes(Svc(home), "telegram", f"Hi\nMEDIA:{home / 'secrets.db'}")
    assert extra == {"media": []} and "voice note not delivered" in body and "sessions folder" in body


@needs_ffmpeg
async def test_wav_becomes_opus_for_telegram_and_mp3_for_email(home: Path) -> None:
    src = wav_in_session(home)
    body, extra = await voice_notes(Svc(home), "telegram", f"Listen:\n{notes.envelope(src, 'audio/wav')}")
    (item,) = extra["media"]
    assert body == "Listen:" and item["mime"] == "audio/ogg" and item["duration_s"] == 1.5
    ogg = Path(item["path"])
    head = (await asyncio.to_thread(ogg.read_bytes))[:200]
    assert ogg == src.parent / "voice" / "1.ogg" and head[:4] == b"OggS" and b"OpusHead" in head
    _, extra = await voice_notes(Svc(home), "email", f"MEDIA:{src}")
    mp3 = Path(extra["media"][0]["path"])
    assert mp3.suffix == ".mp3" and extra["media"][0]["mime"] == "audio/mpeg"
    # an Opus .ogg already in place is delivered as is
    _, again = await voice_notes(Svc(home), "discord", f"MEDIA:{ogg}")
    assert again["media"][0]["path"] == str(ogg)


async def test_missing_ffmpeg_is_an_honest_error(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = wav_in_session(home)
    monkeypatch.setattr(audio.shutil, "which", lambda name: None)
    body, extra = await voice_notes(Svc(home), "telegram", f"Hi\nMEDIA:{src}")
    assert extra == {"media": []}
    assert body.startswith(
        "Hi\n\n(voice note not delivered: cannot encode the voice note: ffmpeg is not installed"
    )


async def test_cap_and_untouched_text(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ogg = home / "sessions" / "s" / "audio" / "n.ogg"
    ogg.parent.mkdir(parents=True)
    ogg.write_bytes(b"OggS" + bytes(4096))
    monkeypatch.setattr(notes, "MAX_BYTES", 1000)
    body, extra = await voice_notes(Svc(home), "telegram", f"MEDIA:{ogg}")
    assert extra == {"media": []} and "16 MiB" in body
    assert await voice_notes(Svc(home), "telegram", "plain text") == ("plain text", {})


class Chat:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict[str, Any]]] = []

    async def __call__(self, chat_id: str, text: str, meta: dict[str, Any]) -> dict[str, Any]:
        self.sent.append((chat_id, text, meta))
        return {"ok": True, "detail": "sent"}


@needs_ffmpeg
async def test_connector_reply_carries_voice_note(services: Services, gateway: FakeGateway) -> None:
    home = services.settings.home
    write_settings_value(home, "plugins.telegram.allowlist", ["42"])
    src = wav_in_session(home)
    gateway.script.append(Scripted(text=f"Here is your note.\n{notes.envelope(src, 'audio/wav')}"))
    chat = Chat()
    out = await InboundRouter(services, "telegram").handle({"chat_id": "42", "text": "say hi"}, chat)
    assert out["ok"]
    chat_id, text, meta = chat.sent[-1]
    assert chat_id == "42" and text == "Here is your note." and meta["media"][0]["mime"] == "audio/ogg"


async def test_stream_true_sends_partials_before_the_final(services: Services, gateway: FakeGateway) -> None:
    home = services.settings.home
    write_settings_value(home, "plugins.telegram.allowlist", ["42"])
    write_settings_value(home, "plugins.telegram.stream", True)
    long = "This answer is streamed to the chat while the model is still writing it. " * 3
    gateway.script.append(Scripted(text=long, delay_s=0.2))
    chat = Chat()
    out = await InboundRouter(services, "telegram").handle({"chat_id": "42", "text": "go"}, chat)
    assert out["ok"]
    partials = [(t, m) for _, t, m in chat.sent if m.get("stream") == "partial"]
    final = [t for _, t, m in chat.sent if "stream" not in m]
    assert partials and all(long.startswith(t) for t, _ in partials) and final == [long]
    assert partials[0][1]["session_id"] == out["session_id"]


async def test_no_stream_without_the_setting(services: Services, gateway: FakeGateway) -> None:
    write_settings_value(services.settings.home, "plugins.telegram.allowlist", ["42"])
    gateway.script.append(Scripted(text="x" * 200, delay_s=0.1))
    chat = Chat()
    await InboundRouter(services, "telegram").handle({"chat_id": "42", "text": "go"}, chat)
    assert [m.get("stream") for _, _, m in chat.sent] == [None]
    await asyncio.sleep(0)
