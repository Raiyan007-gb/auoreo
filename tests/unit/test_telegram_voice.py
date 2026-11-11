"""M7 Wave 2: Telegram inbound voice notes, in process, against a mocked Bot API."""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.unit.test_telegram_plugin import TOKEN, FakeHost, load


class RpcRefusal(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class VoiceHost(FakeHost):
    def __init__(self, home: Path, reply: Any = None) -> None:
        super().__init__(home)
        self.reply = reply if reply is not None else {"text": "slash approve all", "duration_s": 3.0}
        self.transcribed: list[dict[str, Any]] = []

    async def voice_transcribe(self, chat_id: str, mime: str, data: bytes) -> dict[str, Any]:
        self.transcribed.append({"chat_id": chat_id, "mime": mime, "bytes": len(data)})
        if isinstance(self.reply, Exception):
            raise self.reply
        return dict(self.reply)

    def message(
        self, connector: str, chat_id: str, user: str, text: str, reply_to: Any = None, media: Any = None
    ) -> None:
        self.messages.append({"chat_id": chat_id, "text": text, "reply_to": reply_to, "media": media})


class Api:
    def __init__(self, audio: bytes = b"OggS-fake-opus") -> None:
        self.audio = audio
        self.calls: list[str] = []
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/file/bot{TOKEN}/voice/file_7.oga":
            self.calls.append("download")
            return httpx.Response(200, content=self.audio)
        method = path.rsplit("/", 1)[1]
        self.calls.append(method)
        body = json.loads(request.content or b"{}")
        if method == "getFile":
            assert body == {"file_id": "F7"}
            return httpx.Response(
                200, json={"ok": True, "result": {"file_id": "F7", "file_path": "voice/file_7.oga"}}
            )
        if method == "sendMessage":
            self.sent.append(body)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
        return httpx.Response(404, json={"ok": False, "description": "Not Found"})


def voice_update(duration: int = 3, size: int = 2000, **note: Any) -> dict[str, Any]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 10,
            "chat": {"id": 42, "type": "private"},
            "from": {"id": 42, "username": "ana"},
            "voice": {
                "file_id": "F7",
                "file_unique_id": "U7",
                "duration": duration,
                "mime_type": "audio/ogg",
                "file_size": size,
                **note,
            },
        },
    }


def make(home: Path, api: Api, host: FakeHost, config: dict[str, Any]) -> Any:
    p = load().Plugin(host, transport=httpx.MockTransport(api))
    p.initialize({"allowlist": ["42"], **config}, [])
    return p


async def test_voice_note_is_downloaded_transcribed_and_forwarded(home: Path) -> None:
    api, host = Api(), VoiceHost(home)
    p = make(home, api, host, {"voice": True})
    await p.handle_update(voice_update())
    assert api.calls == ["getFile", "download"]
    assert host.transcribed == [{"chat_id": "42", "mime": "audio/ogg", "bytes": len(api.audio)}]
    assert host.messages == [
        {
            "chat_id": "42",
            "text": "slash approve all",
            "reply_to": {"message_id": 10},
            "media": [{"kind": "voice", "mime": "audio/ogg", "duration_s": 3.0}],
        }
    ]


async def test_voice_ignored_without_config(home: Path) -> None:
    api, host = Api(), VoiceHost(home)
    p = make(home, api, host, {})
    await p.handle_update(voice_update())
    assert api.calls == [] and host.messages == [] and host.transcribed == []


async def test_long_or_large_note_is_refused_before_download(home: Path) -> None:
    api, host = Api(), VoiceHost(home)
    p = make(home, api, host, {"voice": True})
    await p.handle_update(voice_update(duration=300))
    await p.handle_update(voice_update(size=6 * 2**20))
    assert api.calls == ["sendMessage", "sendMessage"]  # two short replies, no getFile
    assert "300 s" in api.sent[0]["text"] and "bytes" in api.sent[1]["text"]
    assert host.transcribed == [] and host.messages == []


async def test_unpaired_voice_gets_pairing_code_not_transcription(home: Path) -> None:
    api, host = Api(), VoiceHost(home)
    p = load().Plugin(host, transport=httpx.MockTransport(api))
    p.initialize({"voice": True}, [])
    await p.handle_update(voice_update())
    assert api.calls == ["sendMessage"] and "Pairing code" in api.sent[0]["text"]
    assert host.transcribed == []


async def test_old_host_and_refusals_reply_without_leaking_the_token(
    home: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    api = Api()
    old = FakeHost(home)  # an older host API: no voice_transcribe
    p = make(home, api, old, {"voice": True})
    await p.handle_update(voice_update())
    assert "not supported" in api.sent[-1]["text"] and old.messages == []

    unknown = VoiceHost(home, reply=RpcRefusal(-32601, "unknown method voice.transcribe"))
    p = make(home, api, unknown, {"voice": True})
    await p.handle_update(voice_update())
    assert "not supported" in api.sent[-1]["text"]

    quota = VoiceHost(home, reply=RpcRefusal(-32003, "the daily voice quota is used up"))
    p = make(home, api, quota, {"voice": True})
    await p.handle_update(voice_update())
    assert "quota" in api.sent[-1]["text"] and quota.messages == []

    failing = Api()

    async def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/file/"):
            raise httpx.ConnectError(f"cannot reach {request.url}")
        return await failing(request)

    host = VoiceHost(home)
    p = load().Plugin(host, transport=httpx.MockTransport(broken))
    p.initialize({"allowlist": ["42"], "voice": True}, [])
    await p.handle_update(voice_update())
    assert "could not be downloaded" in failing.sent[-1]["text"]
    every = json.dumps([*api.sent, *failing.sent]) + caplog.text
    assert TOKEN not in every and base64.b64encode(TOKEN.encode()).decode() not in every
