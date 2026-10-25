"""`/api/voice/meetings` (F4) with the fake stt/vad/diarize backends."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from asgi_lifespan import LifespanManager

from auoreo.api.app import create_app
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.voice.audio import wav_from_pcm
from auoreo.voice.meeting import meetings_for
from tests.audio import tone_pcm
from tests.integration.test_api import parse_sse

FAKES = {"stt": "fake", "tts": "fake", "vad": "fake", "wake": "fake", "desktop": "fake", "diarize": "fake"}
SECRET = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"


@pytest.fixture
def settings(home: Path) -> Settings:
    return Settings(home=home, gateway="fake", approval_timeout_s=5, backends=dict(FAKES))


@pytest.fixture
async def client(services: Services, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c


def chunk(freq: float, amp: float, comment: str, seconds: float = 1.0) -> bytes:
    pcm = tone_pcm(seconds, freq=freq, amp=amp) + b"\0\0" * 8000  # 0.5 s of trailing silence
    return wav_from_pcm(pcm, comment=comment)


async def upload(client: httpx.AsyncClient, mid: str, wav: bytes, name: str = "c.wav") -> httpx.Response:
    return await client.post(f"/api/voice/meetings/{mid}/audio", files={"file": (name, wav, "audio/wav")})


async def test_meeting_lifecycle(client: httpx.AsyncClient, services: Services) -> None:
    sess = await services.create_session(title="standup")
    r = await client.post("/api/voice/meetings", json={"title": "Standup", "session_id": sess.id})
    assert r.status_code == 201, r.text
    mid = r.json()["meeting_id"]
    assert mid.startswith("mtg_")

    one = await upload(client, mid, chunk(440, 0.5, "Let's ship the release on Friday."))
    assert one.status_code == 200, one.text
    assert one.json()["index"] == 1 and one.json()["duration_s"] == pytest.approx(1.5, abs=0.01)
    two = await upload(client, mid, chunk(1200, 0.25, f"I will update the changelog, my key is {SECRET}"))
    assert two.json() == {"meeting_id": mid, "index": 2, "duration_s": 1.5, "total_s": 3.0}
    folder = services.settings.home / "meetings" / mid
    assert sorted(p.name for p in (folder / "chunks").iterdir()) == ["0001.wav", "0002.wav"]

    listed = (await client.get("/api/voice/meetings")).json()
    assert [m["id"] for m in listed] == [mid] and listed[0]["status"] == "recording"

    stop = await client.post(f"/api/voice/meetings/{mid}/stop")
    assert stop.status_code == 200, stop.text
    body = stop.json()
    assert body["status"] == "finalised" and body["speakers"] == ["SPEAKER_00", "SPEAKER_01"]
    segs = body["transcript"]
    assert [s["speaker"] for s in segs] == ["SPEAKER_00", "SPEAKER_01"]
    assert segs[0]["text"] == "Let's ship the release on Friday."
    assert segs[0]["start"] == pytest.approx(0.0, abs=0.05)
    assert segs[1]["start"] == pytest.approx(1.5, abs=0.05)
    assert SECRET not in stop.text and "sk-ant" in segs[1]["text"]  # redacted (masked)
    assert all(s["tone"] in {"neutral", "frustrated", "urgent", "confused", "positive"} for s in segs)
    assert "**SPEAKER_01**" in body["markdown"] and SECRET not in body["markdown"]

    # storage: transcript json + markdown; audio deleted (voice_meeting_keep_audio = false)
    assert (folder / "meeting.json").is_file() and (folder / "transcript.md").is_file()
    assert not (folder / "chunks").exists()
    assert SECRET not in (folder / "meeting.json").read_text() + (folder / "transcript.md").read_text()

    # ingested into memory with provenance
    assert body["ingested"] is True
    assert services.memory is not None
    hits = await services.memory.search("ship the release on Friday", limit=5)
    assert any(h.source == f"meeting:{mid}" for h in hits)

    again = await client.post(f"/api/voice/meetings/{mid}/stop")
    assert again.status_code == 409
    late = await upload(client, mid, chunk(440, 0.5, "late"))
    assert late.status_code == 409

    got = (await client.get(f"/api/voice/meetings/{mid}")).json()
    assert got["transcript"] == segs and got["status"] == "finalised"

    summ = await client.post(f"/api/voice/meetings/{mid}/summarize")
    assert summ.status_code == 200, summ.text
    run_id, summary_sid = summ.json()["run_id"], summ.json()["session_id"]
    assert run_id.startswith("run_") and summary_sid != sess.id
    row = await services.get_session(summary_sid)
    assert row is not None and row.workflow == "meeting-summary" and row.agent == "meeting_summarizer"
    await services.coordinator.wait(summary_sid, 10)
    for _ in range(50):
        if (await client.get(f"/api/voice/meetings/{mid}")).json()["summary"]:
            break
        await asyncio.sleep(0.05)
    final = (await client.get(f"/api/voice/meetings/{mid}")).json()
    assert final["summary"] and final["summary_session_id"] == summary_sid
    assert (folder / "summary.md").is_file()

    kinds: list[str] = []
    for _ in range(50):
        q = {"follow": "false", "session_id": sess.id}
        evs = parse_sse((await client.get("/api/events", params=q)).text)
        kinds = [e["type"] for e in evs if e["type"].startswith("meeting.")]
        if "meeting.summarised" in kinds:
            break
        await asyncio.sleep(0.05)
    assert kinds[:4] == ["meeting.started", "meeting.chunk", "meeting.chunk", "meeting.finalised"]
    assert "meeting.summarised" in kinds


async def test_single_recording_keep_audio_and_errors(client: httpx.AsyncClient, services: Services) -> None:
    services.settings.voice_meeting_keep_audio = True
    services.settings.voice_meeting_ingest = False
    mid = (await client.post("/api/voice/meetings", json={"title": "Solo"})).json()["meeting_id"]
    pcm = tone_pcm(1.0, freq=440, amp=0.5) + b"\0\0" * 9600 + tone_pcm(1.0, freq=1200, amp=0.25)
    assert (await upload(client, mid, wav_from_pcm(pcm))).status_code == 200
    body = (await client.post(f"/api/voice/meetings/{mid}/stop")).json()
    assert [s["speaker"] for s in body["transcript"]] == ["SPEAKER_00", "SPEAKER_01"]
    assert body["transcript"][0]["text"].startswith("fake transcript")
    assert body["ingested"] is False
    assert (services.settings.home / "meetings" / mid / "chunks" / "0001.wav").is_file()

    assert (await client.get("/api/voice/meetings/mtg_000000000000")).status_code == 404
    assert (await client.get("/api/voice/meetings/mtg_..secrets")).status_code == 404
    assert (await client.post("/api/voice/meetings", json={"session_id": "ses_nope"})).status_code == 404
    empty = (await client.post("/api/voice/meetings", json={"title": "Empty"})).json()["meeting_id"]
    assert (await client.post(f"/api/voice/meetings/{empty}/summarize")).status_code == 409
    done = (await client.post(f"/api/voice/meetings/{empty}/stop")).json()
    assert done["status"] == "finalised" and done["transcript"] == []


async def test_live_rtc_utterances_append_instead_of_turns(services: Services) -> None:
    meetings = meetings_for(services)
    meta = await meetings.start("Live", rtc_id="rtc_1")
    assert meta["live"] is True
    pcm = tone_pcm(1.0, freq=440, amp=0.5)
    assert await meetings.append_live("rtc_1", pcm, 16_000) is False  # listen-only: not submitted
    assert meetings.load(meta["id"])["chunks"] == 1
    assert await meetings.append_live("rtc_other", pcm, 16_000) is True  # not a meeting peer

    from auoreo.voice import voice_for

    voice = voice_for(services)
    voice.attach(type("M", (), {"peers": {}, "on_audio_frames": None})())
    assert voice.bridge is not None
    submitted: list[str] = []

    async def fake_submit(*a: object, **k: object) -> str:
        submitted.append(str(a[1]))
        return "run_x"

    services.coordinator.submit = fake_submit  # type: ignore[method-assign]
    sess = await services.create_session()
    await voice.bridge._utterance("rtc_1", sess.id, pcm, 16_000)
    assert submitted == [] and meetings.load(meta["id"])["chunks"] == 2

    speaking = await meetings.start("Assist", speak=True, rtc_id="rtc_2")
    await voice.bridge._utterance("rtc_2", sess.id, pcm, 16_000)
    assert meetings.load(speaking["id"])["chunks"] == 1 and len(submitted) == 1  # P2: also a turn
    await voice.close()

    stopped = await meetings.stop(meta["id"])
    assert stopped["status"] == "finalised" and stopped["live"] is False
    assert "rtc_1" not in meetings.live
