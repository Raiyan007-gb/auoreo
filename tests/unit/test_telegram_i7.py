"""I7: plugins/telegram streaming (sendMessageDraft / edit), sendVoice, token lock, Mini-App
initData verification — in process against a mocked Bot API (`httpx.MockTransport`)."""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import urlencode

import httpx
import pytest

PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "telegram" / "plugin.py"
TOKEN = "123:abc"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tg_i7_under_test", PLUGIN)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Host:
    def __init__(self) -> None:
        self.logs: list[str] = []
        self.audits: list[tuple[str, dict[str, Any]]] = []

    async def secret(self, name: str) -> str | None:
        return TOKEN

    def log(self, level: str, message: str) -> None:
        self.logs.append(message)

    async def audit(self, action: str, detail: dict[str, Any], decision: str = "") -> None:
        self.audits.append((action, detail))


class Api:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[1]
        ctype = request.headers.get("content-type", "")
        body: Any = json.loads(request.content) if ctype.startswith("application/json") else request.content
        self.calls.append((method, body))
        result: Any = True if method == "sendMessageDraft" else {"message_id": 100 + len(self.calls)}
        return httpx.Response(200, json={"ok": True, "result": result})

    def methods(self) -> list[str]:
        return [m for m, _ in self.calls]


def plugin(cfg: dict[str, Any] | None = None) -> tuple[Any, Api]:
    api = Api()
    p = load().Plugin(Host(), transport=httpx.MockTransport(api))
    p.initialize(cfg or {}, [])
    return p, api


async def test_private_chat_streams_drafts_then_persists() -> None:
    p, api = plugin({"stream_interval_s": 0.3})
    r1 = await p.send("telegram", "42", "Hello", {"stream": "partial"})
    r2 = await p.send("telegram", "42", "Hello wor", {"stream": "partial"})  # inside the interval
    assert r1["detail"] == "stream draft" and r2["detail"] == "rate-limited"
    p.streams["42"]["at"] -= 1.0  # pretend the interval passed
    await p.send("telegram", "42", "Hello world", {"stream": "partial"})
    drafts = [b for m, b in api.calls if m == "sendMessageDraft"]
    assert [d["text"] for d in drafts] == ["Hello", "Hello world"]
    assert drafts[0]["draft_id"] == drafts[1]["draft_id"] != 0 and drafts[0]["chat_id"] == "42"
    final = await p.send("telegram", "42", "Hello world!", {})
    assert final["ok"] and api.calls[-1] == ("sendMessage", {"chat_id": "42", "text": "Hello world!"})
    assert "42" not in p.streams


async def test_group_streams_by_editing_one_message() -> None:
    p, api = plugin({"stream_interval_s": 0.3})
    await p.send("telegram", "-100", "partial one", {"stream": "partial"})
    p.streams["-100"]["at"] -= 1.0
    await p.send("telegram", "-100", "partial one two", {"stream": "partial"})
    long_final = "x" * 3990 + "\n" + "tail " * 10
    res = await p.send("telegram", "-100", long_final, {})
    assert res["ok"] and res["detail"] == "2 message(s)"
    assert api.methods() == ["sendMessage", "editMessageText", "editMessageText", "sendMessage"]
    first_id = api.calls[1][1]["message_id"]
    assert api.calls[2][1]["message_id"] == first_id and api.calls[2][1]["text"].startswith("xxx")
    assert api.calls[3][1]["text"].startswith("tail")


async def test_send_voice_multipart_with_caption(tmp_path: Path) -> None:
    p, api = plugin()
    note = tmp_path / "n.ogg"
    note.write_bytes(b"OggS" + bytes(50))
    media = [{"path": str(note), "mime": "audio/ogg", "duration_s": 2.6}]
    res = await p.send("telegram", "42", "short caption", {"media": media, "reply_to": {"thread_id": 9}})
    assert res["ok"] and res["detail"] == "1 message(s)" and api.methods() == ["sendVoice"]
    body = api.calls[0][1]
    assert b'name="voice"; filename="n.ogg"' in body and b"audio/ogg" in body
    assert b'name="duration"\r\n\r\n3' in body and b"short caption" in body
    assert b'name="message_thread_id"\r\n\r\n9' in body
    # a long text goes as its own message after the voice note
    res = await p.send("telegram", "42", "y" * 2000, {"media": media})
    assert api.methods()[-2:] == ["sendVoice", "sendMessage"] and b"caption" not in api.calls[-2][1]
    big = tmp_path / "big.ogg"
    big.write_bytes(bytes(17 * 2**20))
    bad = await p.send("telegram", "42", "", {"media": [{"path": str(big)}]})
    assert bad["ok"] is False and "cap" in bad["detail"]


def test_verify_init_data_accepts_valid_and_rejects_tampered() -> None:
    mod = load()
    fields = {"auth_date": "1700000000", "query_id": "AAH", "user": json.dumps({"id": 7, "first_name": "Al"})}
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    good = urlencode(fields | {"hash": hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()})
    out = mod.verify_init_data(good, TOKEN, now=1700000100)
    assert out["user"]["id"] == 7 and out["query_id"] == "AAH"
    with pytest.raises(mod.TelegramError, match="mismatch"):
        mod.verify_init_data(good.replace("AAH", "AAX"), TOKEN, now=1700000100)
    with pytest.raises(mod.TelegramError, match="mismatch"):
        mod.verify_init_data(good, "999:other", now=1700000100)
    with pytest.raises(mod.TelegramError, match="too old"):
        mod.verify_init_data(good, TOKEN, now=1700000000 + 2 * 86400)
    with pytest.raises(mod.TelegramError, match="no hash"):
        mod.verify_init_data("auth_date=1", TOKEN)


def test_token_lock_one_holder_and_stale_takeover(tmp_path: Path) -> None:
    mod = load()
    a = mod.TokenLock(TOKEN, str(tmp_path))
    assert a.acquire() and a.path.read_text() == str(os.getpid())
    assert TOKEN not in a.path.name  # the file name carries a hash, never the token
    # a live foreign holder blocks
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        a.path.write_text(str(child.pid))
        b = mod.TokenLock(TOKEN, str(tmp_path))
        assert b.acquire() is False and b.holder() == child.pid
    finally:
        child.kill()
        child.wait()
    # the holder died → stale → taken over
    assert b.acquire() is True and b.holder() == os.getpid()
    b.release()
    assert not b.path.exists()
    assert mod.TokenLock("other", str(tmp_path)).path != b.path


async def test_connector_start_refuses_when_token_locked(tmp_path: Path) -> None:
    mod = load()
    p, _ = plugin({"lock_dir": str(tmp_path)})
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])  # noqa: ASYNC220
    try:
        mod.TokenLock(TOKEN, str(tmp_path)).path.write_text(str(child.pid))
        res = await p.connector_start("telegram")
        assert res["ok"] is False and "one poller per token" in res["detail"] and p._poll is None
        assert p.host.audits == [("token_locked", {"holder_pid": child.pid})]
    finally:
        child.kill()
        child.wait()
    res = await p.connector_start("telegram")  # stale now
    assert res == {} and p._poll is not None
    await p.shutdown()
    assert not mod.TokenLock(TOKEN, str(tmp_path)).path.exists()
