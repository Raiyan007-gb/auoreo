"""I7: plugins/email IMAP STARTTLS + XOAUTH2 (in-process TLS IMAP server with a throwaway CA),
SMTP STARTTLS + XOAUTH2 (aiosmtpd), MP3 attachment delivery; plugins/calendar Google Calendar
v3 list/create (mocked REST, OAuth bearer from the host, never overwrites)."""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import ipaddress
import json
import socket
import ssl
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.unit import test_h5_connectors as h5

TOKEN = "ya29.access-token"
USER = "bot@example.com"


def make_tls(tmp_path: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """(server context, client context trusting it) for 127.0.0.1."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    client = ssl.create_default_context(cafile=str(cert_path))
    return server, client


class OAuthHost(h5.FakeHost):
    def __init__(self, token: str | None = TOKEN) -> None:
        super().__init__({})
        self.token = token
        self.oauth_asked: list[str] = []

    async def oauth_token(self, connector: str) -> str | None:
        self.oauth_asked.append(connector)
        if self.token is None:
            raise RuntimeError("not signed in to google: run `auoreo login google`")
        return self.token


class TlsImap(h5.FakeImap):
    """FakeImap + STARTTLS + AUTHENTICATE XOAUTH2 (the SASL failure path sends a `+` challenge)."""

    def __init__(self, ctx: ssl.SSLContext) -> None:
        super().__init__(USER, "unused")
        self.ctx = ctx
        self.tls = False

    async def _handle(self, tag: str, rest: str, r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        if rest == "STARTTLS":
            w.write(f"{tag} OK begin TLS\r\n".encode())
            await w.drain()
            await w.start_tls(self.ctx)
            self.tls = True
            return
        if rest.startswith("AUTHENTICATE XOAUTH2 "):
            raw = base64.b64decode(rest.split()[2]).decode()
            if self.tls and raw == f"user={USER}\x01auth=Bearer {TOKEN}\x01\x01":
                w.write(f"{tag} OK authenticated\r\n".encode())
            else:
                w.write(b"+ eyJzdGF0dXMiOiI0MDEifQ==\r\n")
                await w.drain()
                await r.readline()  # the client's empty response
                w.write(f"{tag} NO [AUTHENTICATIONFAILED] invalid credentials\r\n".encode())
            await w.drain()
            return
        await super()._handle(tag, rest, r, w)


CFG = h5.EMAIL_CFG | {"imap_security": "starttls", "auth": "xoauth2", "smtp_security": "starttls"}


async def test_imap_starttls_xoauth2_delivers_mail(tmp_path: Path) -> None:
    server_ctx, client_ctx = make_tls(tmp_path)
    imap = TlsImap(server_ctx)
    port = await imap.start()
    imap.msgs[1] = h5.mail("Boss <boss@example.com>", "Hi", "over TLS")
    host = OAuthHost()
    plugin = h5.load("email").Plugin(host)
    plugin.ssl_context = client_ctx
    plugin.initialize(CFG | {"imap_port": port}, [])
    await plugin.connector_start("email")
    try:
        await asyncio.wait_for(host.got.wait(), 5)
        assert "over TLS" in host.messages[0]["text"] and imap.tls
        assert imap.commands[:2] == ["STARTTLS", "AUTHENTICATE"] and "LOGIN" not in imap.commands
        assert host.oauth_asked == ["google"] and host.asked == []  # no password secret read
    finally:
        await plugin.shutdown()
        await imap.stop()


async def test_imap_xoauth2_rejection_is_reported(tmp_path: Path) -> None:
    server_ctx, client_ctx = make_tls(tmp_path)
    imap = TlsImap(server_ctx)
    port = await imap.start()
    mod = h5.load("email")
    plugin = mod.Plugin(OAuthHost("wrong-token"))
    plugin.ssl_context = client_ctx
    plugin.initialize(CFG | {"imap_port": port}, [])
    try:
        with pytest.raises(mod.EmailError, match="AUTHENTICATE failed: NO"):
            await plugin.open_imap()
        unsigned = mod.Plugin(OAuthHost(None))
        unsigned.initialize(CFG | {"imap_port": port}, [])
        with pytest.raises(mod.EmailError, match="auoreo login google"):
            await unsigned.credential()
    finally:
        await imap.stop()


async def test_smtp_starttls_xoauth2_and_voice_attachment(tmp_path: Path) -> None:
    aiosmtpd = pytest.importorskip("aiosmtpd.controller")
    from aiosmtpd.smtp import AuthResult

    server_ctx, client_ctx = make_tls(tmp_path)
    received: list[Any] = []
    auths: list[str] = []

    class Handler:
        async def handle_DATA(self, server: Any, session: Any, envelope: Any) -> str:  # noqa: N802
            received.append(envelope)
            return "250 OK"

        async def auth_XOAUTH2(self, server: Any, args: list[str]) -> AuthResult:  # noqa: N802
            raw = base64.b64decode(args[1]).decode()
            auths.append(raw)
            return AuthResult(success=raw == f"user={USER}\x01auth=Bearer {TOKEN}\x01\x01", handled=False)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    ctrl = aiosmtpd.Controller(
        Handler(), hostname="127.0.0.1", port=port, tls_context=server_ctx, require_starttls=True
    )
    ctrl.start()
    try:
        plugin = h5.load("email").Plugin(OAuthHost())
        plugin.ssl_context = client_ctx
        plugin.initialize(CFG | {"smtp_port": port}, [])
        out = await plugin.call_tool(
            "send_email", {"to": ["boss@example.com"], "subject": "S", "body": "B"}, "s"
        )
        assert not out["is_error"], out
        assert auths == [f"user={USER}\x01auth=Bearer {TOKEN}\x01\x01"] and received
        note = tmp_path / "note.mp3"
        note.write_bytes(b"ID3" + bytes(64))
        meta = {"media": [{"path": str(note), "mime": "audio/mpeg", "duration_s": 1.0}]}
        res = await plugin.send("email", "boss@example.com", "voice note attached", meta)
        assert res["ok"], res
        raw = received[-1].content
        assert b"Content-Type: audio/mpeg" in raw and b'filename="note.mp3"' in raw
        bad = h5.load("email").Plugin(OAuthHost("nope"))
        bad.ssl_context = client_ctx
        bad.initialize(CFG | {"smtp_port": port}, [])
        fail = await bad.send("email", "boss@example.com", "x", {})
        assert fail["ok"] is False and "535" in fail["detail"]
    finally:
        ctrl.stop()


# ------------------------------------------------------------------ calendar (google)
class GoogleCal:
    def __init__(self) -> None:
        self.events: dict[str, dict[str, Any]] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.url.raw_path.split(b"?")[0] == b"/calendar/v3/calendars/team%40example.com/events"
        if request.method == "POST":
            body = json.loads(request.content)
            if body["id"] in self.events:
                return httpx.Response(409, json={"error": {"code": 409, "message": "duplicate"}})
            self.events[body["id"]] = body
            return httpx.Response(200, json=body | {"status": "confirmed"})
        q = request.url.params
        assert q["singleEvents"] == "true" and q["orderBy"] == "startTime" and q["timeMin"] < q["timeMax"]
        items = sorted(self.events.values(), key=lambda e: e["start"]["dateTime"])
        return httpx.Response(200, json={"items": items + [{"id": "allday", "summary": "Holiday",
                                                             "start": {"date": "2026-11-03"},
                                                             "end": {"date": "2026-11-04"}}]})  # fmt: skip


GCFG = {"provider": "google", "calendar_id": "team@example.com", "timezone": "Europe/Berlin"}


async def test_google_calendar_create_and_list() -> None:
    cal = GoogleCal()
    host = OAuthHost()
    plugin = h5.load("calendar").Plugin(host, transport=httpx.MockTransport(cal))
    plugin.initialize(GCFG, [])
    out = await plugin.call_tool(
        "calendar_create_event",
        {"summary": "Planning", "start": "2026-11-02T10:00", "location": "Room 1", "description": "Q4"},
        "s",
    )
    assert not out["is_error"], out
    ((eid, body),) = cal.events.items()
    assert len(eid) == 32 and set(eid) <= set("0123456789abcdef")  # base32hex-compatible id
    assert body["start"] == {"dateTime": "2026-11-02T10:00:00+01:00", "timeZone": "Europe/Berlin"}
    assert body["end"]["dateTime"] == "2026-11-02T11:00:00+01:00" and body["location"] == "Room 1"
    assert host.audits[-1] == ("created", {"uid": eid})
    listed = await plugin.call_tool("calendar_list_events", {"start": "2026-11-01", "end": "2026-11-08"}, "s")
    assert not listed["is_error"] and "Planning" in listed["content"] and "@ Room 1" in listed["content"]
    assert "2026-11-03 → 2026-11-04: Holiday" in listed["content"]
    assert host.oauth_asked == ["google", "google"] and host.asked == []  # no CalDAV password


async def test_google_calendar_never_overwrites_and_reports_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    cal = GoogleCal()
    mod = h5.load("calendar")
    plugin = mod.Plugin(OAuthHost(), transport=httpx.MockTransport(cal))
    plugin.initialize(GCFG, [])
    fixed = type("U", (), {"hex": "a" * 32})()
    monkeypatch.setattr(mod.uuid, "uuid4", lambda: fixed)
    assert not (
        await plugin.call_tool("calendar_create_event", {"summary": "a", "start": "2026-11-02T10:00"}, "s")
    )["is_error"]
    again = await plugin.call_tool(
        "calendar_create_event", {"summary": "b", "start": "2026-11-02T12:00"}, "s"
    )
    assert again["is_error"] and "nothing was overwritten" in again["content"]
    assert cal.events["a" * 32]["summary"] == "a" and all(r.method != "PUT" for r in cal.requests)
    denied = mod.Plugin(OAuthHost(None), transport=httpx.MockTransport(cal))
    denied.initialize(GCFG, [])
    out = await denied.call_tool("calendar_list_events", {}, "s")
    assert out["is_error"] and "auoreo login google" in out["content"]
    broken = mod.Plugin(OAuthHost(), transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad")))
    broken.initialize(GCFG | {"calendar_id": "team@example.com"}, [])
    out = await broken.call_tool("calendar_list_events", {}, "s")
    assert out["is_error"] and "HTTP 401" in out["content"]
