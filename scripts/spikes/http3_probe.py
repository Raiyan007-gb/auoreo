"""F7 spike: can Hypercorn serve the AUOREO ASGI app over HTTP/3 (QUIC), including SSE and
WebSockets? See docs/spikes/http3.md. Not a dependency: run it in a throwaway environment.

    uv run --with 'hypercorn[h3]' python scripts/spikes/http3_probe.py [--requests 50]

What it does, all on 127.0.0.1 with a fresh AUOREO_HOME and the fake gateway:
1. self-signed cert (cryptography), Hypercorn with `bind` (TCP+TLS: h1/h2) and `quic_bind`
   (UDP: h3) on the same app (`auoreo.api.app.create_app`);
2. an aioquic HTTP/3 client: `GET /api/health`; `GET /api/events` (SSE) held open while
   `POST /api/sessions` on another stream must surface `session.created` on the SSE stream;
   `CONNECT /ws` with `:protocol websocket` (RFC 9220) and a wsproto frame reader expecting
   the `connected` frame;
3. checks the `alt-svc` header on the TCP side and times N sequential `/api/health`
   requests over h3 and over h2 (httpx, same server) for a loopback latency sanity check.
Prints one JSON object with every result (exit 0 even when a check fails: it is a probe).
`--target PORT` skips the embedded Hypercorn and probes an already running TLS+QUIC edge
instead (see scripts/spikes/http3_caddy_e2e.sh for Caddy in front of `auoreo serve`).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import os
import socket
import ssl
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def free_port(kind: int) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def self_signed(folder: Path) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    certfile, keyfile = folder / "cert.pem", folder / "key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return certfile, keyfile


# ----------------------------------------------------------------- h3 client
def make_client_class() -> Any:
    from aioquic.asyncio.protocol import QuicConnectionProtocol
    from aioquic.h3.connection import H3Connection

    class H3Client(QuicConnectionProtocol):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.h3 = H3Connection(self._quic)
            self.queues: dict[int, asyncio.Queue[Any]] = {}

        def quic_event_received(self, event: Any) -> None:
            for ev in self.h3.handle_event(event):
                q = self.queues.get(getattr(ev, "stream_id", -1))
                if q is not None:
                    q.put_nowait(ev)

        def open(
            self, method: str, path: str, *, extra: list[tuple[bytes, bytes]] | None = None, body: bytes = b""
        ) -> tuple[int, asyncio.Queue[Any]]:
            sid = self._quic.get_next_available_stream_id()
            q: asyncio.Queue[Any] = asyncio.Queue()
            self.queues[sid] = q
            headers = [
                (b":method", method.encode()),
                (b":scheme", b"https"),
                (b":authority", b"localhost"),
                (b":path", path.encode()),
                *(extra or []),
            ]
            streaming = method == "CONNECT"
            self.h3.send_headers(sid, headers, end_stream=not body and not streaming)
            if body:
                self.h3.send_data(sid, body, end_stream=True)
            self.transmit()
            return sid, q

        def send(self, sid: int, data: bytes) -> None:
            self.h3.send_data(sid, data, end_stream=False)
            self.transmit()

    return H3Client


async def read_response(q: asyncio.Queue[Any], timeout: float = 10.0) -> tuple[int, dict[str, str], bytes]:
    from aioquic.h3.events import DataReceived, HeadersReceived

    status, headers, body = 0, {}, b""
    while True:
        ev = await asyncio.wait_for(q.get(), timeout)
        if isinstance(ev, HeadersReceived):
            headers = {k.decode(): v.decode() for k, v in ev.headers}
            status = int(headers.get(":status", "0"))
        elif isinstance(ev, DataReceived):
            body += ev.data
        if getattr(ev, "stream_ended", False):
            return status, headers, body


async def until_data(q: asyncio.Queue[Any], needle: bytes, timeout: float) -> tuple[int, bytes]:
    from aioquic.h3.events import DataReceived, HeadersReceived

    status, buf = 0, b""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while needle not in buf:
        ev = await asyncio.wait_for(q.get(), max(0.01, end - loop.time()))
        if isinstance(ev, HeadersReceived):
            status = int(dict(ev.headers).get(b":status", b"0"))
        elif isinstance(ev, DataReceived):
            buf += ev.data
    return status, buf


@contextlib.asynccontextmanager
async def connect4(port: int) -> Any:
    """`aioquic.asyncio.connect` binds an AF_INET6 dual-stack socket, which fails where IPv6
    is disabled (this container: EAFNOSUPPORT). Same steps over an IPv4 datagram endpoint."""
    from aioquic.h3.connection import H3_ALPN
    from aioquic.quic.configuration import QuicConfiguration
    from aioquic.quic.connection import QuicConnection

    cfg = QuicConfiguration(
        is_client=True, alpn_protocols=H3_ALPN, verify_mode=ssl.CERT_NONE, server_name="localhost"
    )
    cls = make_client_class()
    transport, proto = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: cls(QuicConnection(configuration=cfg)), local_addr=("127.0.0.1", 0)
    )
    try:
        proto.connect(("127.0.0.1", port))
        await asyncio.wait_for(proto.wait_connected(), 10)
        yield proto
    finally:
        proto.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proto.wait_closed(), 5)
        transport.close()


async def h3_checks(udp: int, n: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    async with connect4(udp) as c:
        status, headers, body = await read_response(c.open("GET", "/api/health")[1])
        out["health"] = {"status": status, "body": json.loads(body or b"{}"), "server": headers.get("server")}

        # SSE: keep the stream open, cause an event on another stream, wait for it.
        _sid, sse_q = c.open("GET", "/api/events?after_seq=0", extra=[(b"accept", b"text/event-stream")])
        st, first = await until_data(sse_q, b"connected", 10)
        created = c.open("POST", "/api/sessions", extra=[(b"content-type", b"application/json")], body=b"{}")[
            1
        ]
        post_status, _h, post_body = await read_response(created)
        try:
            st2, buf = await until_data(sse_q, b"session.created", 10)
            out["sse"] = {"status": st, "connected_comment": True, "session_created_seen": True}
        except TimeoutError:
            out["sse"] = {"status": st, "connected_comment": True, "session_created_seen": False}
        out["sse"]["post_status"] = post_status
        out["sse"]["first_bytes"] = first[:60].decode(errors="replace")

        out["websocket"] = await ws_check(c)

        lat = []
        for _ in range(n):
            t0 = time.perf_counter()
            await read_response(c.open("GET", "/api/health")[1])
            lat.append((time.perf_counter() - t0) * 1000)
        out["h3_health_ms"] = {"n": n, "median": round(statistics.median(lat), 2), "p95": p95(lat)}
    return out


async def ws_check(c: Any) -> dict[str, Any]:
    from aioquic.h3.events import DataReceived, HeadersReceived
    from wsproto.frame_protocol import FrameProtocol

    extra = [(b":protocol", b"websocket"), (b"sec-websocket-version", b"13")]
    try:
        _sid, q = c.open("CONNECT", "/ws", extra=extra)
    except Exception as exc:  # aioquic may refuse to send it
        return {"ok": False, "error": f"client: {type(exc).__name__}: {exc}"}
    proto = FrameProtocol(client=True, extensions=[])
    status, frames = 0, []
    try:
        loop = asyncio.get_running_loop()
        end = loop.time() + 10
        while not frames:
            ev = await asyncio.wait_for(q.get(), max(0.01, end - loop.time()))
            if isinstance(ev, HeadersReceived):
                status = int(dict(ev.headers).get(b":status", b"0"))
                if status != 200:
                    break
            elif isinstance(ev, DataReceived):
                proto.receive_bytes(ev.data)
                frames += [f.payload for f in proto.received_frames()]
            if getattr(ev, "stream_ended", False):
                break
    except TimeoutError:
        return {"ok": False, "status": status, "error": "timeout waiting for the `connected` frame"}
    first = frames[0] if frames else ""
    text = first if isinstance(first, str) else bytes(first).decode(errors="replace")
    return {"ok": status == 200 and '"connected"' in text, "status": status, "first_frame": text[:120]}


def p95(values: list[float]) -> float:
    return round(sorted(values)[max(0, int(len(values) * 0.95) - 1)], 2)


async def tcp_checks(tcp: int, n: int) -> dict[str, Any]:
    import httpx

    out: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(http2=True, verify=False, base_url=f"https://localhost:{tcp}") as cl:
            r = await cl.get("/api/health")
            out["tcp_health"] = {"status": r.status_code, "http_version": r.http_version}
            out["alt_svc"] = r.headers.get("alt-svc")
            lat = []
            for _ in range(n):
                t0 = time.perf_counter()
                await cl.get("/api/health")
                lat.append((time.perf_counter() - t0) * 1000)
            out["h2_health_ms"] = {"n": n, "median": round(statistics.median(lat), 2), "p95": p95(lat)}
    except Exception as exc:
        out["tcp_error"] = f"{type(exc).__name__}: {exc}"
    out["tcp_websocket"] = await tcp_ws_check(tcp)
    return out


async def tcp_ws_check(tcp: int) -> dict[str, Any]:
    """`wss://` over TCP (HTTP/1.1 Upgrade): what browsers use even when the page is on h3."""
    try:
        from websockets.asyncio.client import connect as ws_connect

        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        async with ws_connect(f"wss://localhost:{tcp}/ws", ssl=ctx, open_timeout=10) as ws:
            first = await asyncio.wait_for(ws.recv(), 10)
        text = first if isinstance(first, str) else first.decode(errors="replace")
        return {"ok": '"connected"' in text, "first_frame": text[:80]}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def probe_only(port: int, n: int) -> dict[str, Any]:
    """`--target PORT`: probe an already running edge (e.g. Caddy in front of uvicorn) that
    serves TLS on TCP and QUIC on UDP at the same port."""
    result: dict[str, Any] = {"python": sys.version.split()[0], "target": f"localhost:{port}"}
    try:
        result.update(await asyncio.wait_for(h3_checks(port, n), 60))
    except Exception as exc:
        result["h3_error"] = f"{type(exc).__name__}: {exc}"
    result.update(await tcp_checks(port, n))
    return result


async def main_async(n: int) -> dict[str, Any]:
    from hypercorn.asyncio import serve
    from hypercorn.config import Config

    from auoreo.api.app import create_app

    tmp = Path(tempfile.mkdtemp(prefix="auoreo-h3-"))
    certfile, keyfile = self_signed(tmp)
    tcp, udp = free_port(socket.SOCK_STREAM), free_port(socket.SOCK_DGRAM)
    cfg = Config()
    cfg.bind = [f"127.0.0.1:{tcp}"]
    cfg.quic_bind = [f"127.0.0.1:{udp}"]
    cfg.certfile, cfg.keyfile = str(certfile), str(keyfile)
    cfg.accesslog = None
    stop = asyncio.Event()
    server = asyncio.create_task(serve(create_app(), cfg, shutdown_trigger=stop.wait))  # type: ignore[arg-type]
    result: dict[str, Any] = {"python": sys.version.split()[0]}
    with contextlib.suppress(Exception):
        from importlib.metadata import version

        result["versions"] = {p: version(p) for p in ("hypercorn", "aioquic", "uvicorn")}
    try:
        await asyncio.sleep(2.0)  # lifespan: Services.create
        try:
            result.update(await asyncio.wait_for(h3_checks(udp, n), 60))
        except Exception as exc:
            result["h3_error"] = f"{type(exc).__name__}: {exc}"
        result.update(await tcp_checks(tcp, n))
    finally:
        stop.set()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(server, 15)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--requests", type=int, default=50)
    ap.add_argument("--target", type=int, help="probe a running TLS+QUIC edge on this port instead")
    args = ap.parse_args()
    if args.target:
        print(json.dumps(asyncio.run(probe_only(args.target, args.requests)), indent=2, default=str))
        return 0
    home = tempfile.mkdtemp(prefix="auoreo-h3-home-")
    os.environ.update({"AUOREO_HOME": home, "AUOREO_GATEWAY": "fake"})
    os.environ.pop("AUOREO_SERVER_PASSWORD", None)
    sys.path.insert(0, str(ROOT / "src"))
    print(json.dumps(asyncio.run(main_async(args.requests)), indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
