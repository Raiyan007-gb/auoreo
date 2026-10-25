"""G5c: VCR cassette hygiene — secrets are filtered on record, committed cassettes are clean."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import vcr
import yaml

from tests.cassettes import CASSETTES, REDACTED, config

KEY = "co-live-" + "Z" * 32
TOKEN = "sk-ant-api03-" + "Q" * 40
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_-]{20,}|co-live-|Bearer (?!REDACTED)\S{8,})")


class _Echo(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 (http.server API)
        length = int(self.headers.get("content-length") or 0)
        self.rfile.read(length)
        body = json.dumps({"ok": True, "echo_token": TOKEN}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("set-cookie", f"session={KEY}")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


async def test_recording_filters_secrets(server: str, tmp_path: Path) -> None:
    rec = vcr.VCR(cassette_library_dir=str(tmp_path), record_mode="all", **config())
    with rec.use_cassette("probe.yaml"):
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{server}/v2/rerank?api_key={KEY}",
                json={"query": "q", "note": f"token={TOKEN}"},
                headers={"Authorization": f"Bearer {KEY}", "X-Api-Key": KEY},
            )
    assert r.json()["echo_token"] == TOKEN  # the live caller still sees the real response
    text = (tmp_path / "probe.yaml").read_text()
    assert KEY not in text and TOKEN not in text
    (interaction,) = yaml.safe_load(text)["interactions"]
    req, resp = interaction["request"], interaction["response"]
    headers = {k.lower(): v for k, v in req["headers"].items()}
    assert headers["authorization"] == [REDACTED] and headers["x-api-key"] == [REDACTED]
    assert f"api_key={REDACTED}" in req["uri"]
    assert not any(k.lower() == "set-cookie" for k in resp["headers"])


async def test_record_mode_none_refuses_unrecorded_requests(server: str, tmp_path: Path) -> None:
    rec = vcr.VCR(cassette_library_dir=str(tmp_path), record_mode="none", **config())
    with rec.use_cassette("empty.yaml"), pytest.raises(vcr.errors.CannotOverwriteExistingCassetteException):
        async with httpx.AsyncClient() as client:
            await client.post(f"{server}/v2/rerank", json={})


def test_committed_cassettes_contain_no_secrets() -> None:
    files = sorted(CASSETTES.rglob("*.yaml"))
    assert files, "expected at least one committed cassette"
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert not SECRET_RE.search(text), f"{path.relative_to(CASSETTES)} looks like it holds a secret"
        for interaction in yaml.safe_load(text)["interactions"]:
            headers = {k.lower(): v for k, v in interaction["request"]["headers"].items()}
            for name in ("authorization", "x-api-key", "cookie"):
                assert headers.get(name, [REDACTED]) == [REDACTED], f"{path.name}: {name} not filtered"
