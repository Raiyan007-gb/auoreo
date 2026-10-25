"""A minimal in-process MQTT 3.1.1 broker for tests (asyncio, stdlib only).

Enough for paho-mqtt clients: CONNECT/CONNACK, SUBSCRIBE/SUBACK (with retained replay),
PUBLISH at QoS 0/1/2 inbound (PUBACK, PUBREC/PUBREL/PUBCOMP), fan-out to subscribers at
min(QoS, 1), PINGREQ and DISCONNECT. `deny` filters are refused in SUBACK (0x80), so a
broker-side ACL can be simulated. Not a real broker: no persistence, no will, no auth.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
from dataclasses import dataclass, field
from typing import Any


def matches(pattern: str, topic: str) -> bool:
    p, t = pattern.split("/"), topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t) or (part != "+" and part != t[i]):
            return False
    return len(p) == len(t)


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        byte, n = n % 128, n // 128
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s: str) -> bytes:
    b = s.encode()
    return len(b).to_bytes(2, "big") + b


def _packet(header: int, body: bytes) -> bytes:
    return bytes([header]) + _varint(len(body)) + body


@dataclass
class Conn:
    writer: asyncio.StreamWriter
    client_id: str = ""
    subs: dict[str, int] = field(default_factory=dict)


@dataclass
class Published:
    topic: str
    payload: bytes
    qos: int
    retain: bool
    client_id: str


class Broker:
    def __init__(self, deny: tuple[str, ...] = ()) -> None:
        self.deny = deny
        self.conns: list[Conn] = []
        self.published: list[Published] = []
        self.retained: dict[str, bytes] = {}
        self.connects: list[dict[str, Any]] = []
        self.port = 0
        self._server: asyncio.Server | None = None
        self._pids = itertools.count(1)

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        for c in list(self.conns):
            c.writer.close()
        if self._server is not None:
            self._server.close()
            with contextlib.suppress(Exception):
                await self._server.wait_closed()

    def subscribed(self, client_id: str) -> dict[str, int]:
        return next((c.subs for c in self.conns if c.client_id == client_id), {})

    async def _read(self, reader: asyncio.StreamReader) -> tuple[int, bytes]:
        header = (await reader.readexactly(1))[0]
        mult, length = 1, 0
        while True:
            byte = (await reader.readexactly(1))[0]
            length += (byte & 0x7F) * mult
            mult *= 128
            if not byte & 0x80:
                break
        return header, await reader.readexactly(length)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        conn = Conn(writer)
        self.conns.append(conn)
        try:
            while True:
                header, body = await self._read(reader)
                await self._handle(conn, header, body)
        except (asyncio.IncompleteReadError, ConnectionError, StopAsyncIteration):
            pass
        finally:
            if conn in self.conns:
                self.conns.remove(conn)
            writer.close()

    async def _handle(self, conn: Conn, header: int, body: bytes) -> None:
        kind = header >> 4
        if kind == 1:  # CONNECT
            name_len = int.from_bytes(body[0:2], "big")
            pos = 2 + name_len + 1  # protocol name + level
            flags = body[pos]
            pos += 3  # flags + keepalive
            cid_len = int.from_bytes(body[pos : pos + 2], "big")
            conn.client_id = body[pos + 2 : pos + 2 + cid_len].decode()
            self.connects.append(
                {
                    "client_id": conn.client_id,
                    "clean_session": bool(flags & 0x02),
                    "username": bool(flags & 0x80),
                }
            )
            conn.writer.write(_packet(0x20, b"\x00\x00"))
        elif kind == 8:  # SUBSCRIBE
            pid, pos, granted = body[0:2], 2, []
            replay: list[tuple[str, bytes]] = []
            while pos < len(body):
                n = int.from_bytes(body[pos : pos + 2], "big")
                topic = body[pos + 2 : pos + 2 + n].decode()
                qos = body[pos + 2 + n]
                pos += 3 + n
                if any(matches(d, topic) for d in self.deny):
                    granted.append(0x80)
                    continue
                conn.subs[topic] = qos
                granted.append(min(qos, 1))
                replay += [(t, p) for t, p in self.retained.items() if matches(topic, t)]
            conn.writer.write(_packet(0x90, pid + bytes(granted)))
            for topic, payload in replay:
                self._deliver(conn, topic, payload, 1, retain=True)
        elif kind == 3:  # PUBLISH
            qos, retain = (header >> 1) & 0x03, bool(header & 0x01)
            n = int.from_bytes(body[0:2], "big")
            topic = body[2 : 2 + n].decode()
            pos = 2 + n
            pid = body[pos : pos + 2] if qos else b""
            payload = body[pos + (2 if qos else 0) :]
            self.published.append(Published(topic, payload, qos, retain, conn.client_id))
            if retain:
                self.retained[topic] = payload
            if qos == 1:
                conn.writer.write(_packet(0x40, pid))
            elif qos == 2:
                conn.writer.write(_packet(0x50, pid))
            for other in list(self.conns):
                best = max((q for f, q in other.subs.items() if matches(f, topic)), default=None)
                if best is not None:
                    self._deliver(other, topic, payload, min(qos, best, 1), retain=False)
        elif kind == 6:  # PUBREL
            conn.writer.write(_packet(0x70, body[0:2]))
        elif kind == 12:  # PINGREQ
            conn.writer.write(_packet(0xD0, b""))
        elif kind == 14:  # DISCONNECT
            raise StopAsyncIteration
        await conn.writer.drain()

    def _deliver(self, conn: Conn, topic: str, payload: bytes, qos: int, *, retain: bool) -> None:
        header = 0x30 | (qos << 1) | (0x01 if retain else 0)
        pid = (next(self._pids) % 65535 + 1).to_bytes(2, "big") if qos else b""
        conn.writer.write(_packet(header, _str(topic) + pid + payload))
