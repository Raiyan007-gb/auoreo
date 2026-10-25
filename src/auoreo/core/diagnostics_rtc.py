"""Teleop / ICE doctor rows (W2-D): STUN/TURN config, a 2 s STUN binding request, and the
selected rtc transport's check(). Registered into `diagnostics.EXTRA_CHECKS`."""

from __future__ import annotations

import asyncio
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING

from auoreo.core.paths import bundled_root
from auoreo.core.settings import Settings
from auoreo.harness.discovery import default_roots
from auoreo.harness.registry import Harness, HarnessError

if TYPE_CHECKING:
    from auoreo.core.diagnostics import Check, Level


STUN_TIMEOUT_S = 2.0


def stun_target(url: str) -> tuple[str, int] | None:
    """`stun:host[:port]` → `(host, port)`; `stuns:`/`turn:` and malformed URLs → None."""
    if not url.startswith("stun:"):
        return None
    hostport = url[5:].split("?", 1)[0]
    host, _, port = hostport.rpartition(":") if hostport.count(":") == 1 else (hostport, "", "")
    try:
        return (host or hostport), int(port or 3478)
    except ValueError:
        return None


def stun_binding(host: str, port: int, timeout: float = STUN_TIMEOUT_S) -> str:
    """RFC 5389 Binding request over UDP; returns the XOR-MAPPED-ADDRESS as `ip:port`."""
    txid = os.urandom(12)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(b"\x00\x01\x00\x00\x21\x12\xa4\x42" + txid, (host, port))
        data, _ = sock.recvfrom(2048)
    if len(data) < 20 or data[:2] != b"\x01\x01" or data[8:20] != txid:
        raise ValueError("not a STUN Binding success response")
    pos = 20
    while pos + 4 <= len(data):
        kind, size = int.from_bytes(data[pos : pos + 2]), int.from_bytes(data[pos + 2 : pos + 4])
        val = data[pos + 4 : pos + 4 + size]
        if kind == 0x0020 and size >= 8 and val[1] == 0x01:  # XOR-MAPPED-ADDRESS, IPv4
            xport = int.from_bytes(val[2:4]) ^ 0x2112
            ip = ".".join(str(b ^ m) for b, m in zip(val[4:8], b"\x21\x12\xa4\x42", strict=True))
            return f"{ip}:{xport}"
        pos += 4 + size + (-size % 4)
    raise ValueError("no XOR-MAPPED-ADDRESS in the response")


async def rtc_checks(st: Settings) -> list[Check]:
    from auoreo.core.diagnostics import Check  # late: diagnostics imports this module

    ice = list(st.rtc_ice_servers)
    stun = [u for u in ice if u.startswith(("stun:", "stuns:"))]
    turn = [u for u in ice if u.startswith(("turn:", "turns:"))]
    out = [
        Check(
            "rtc:ice",
            "ok" if ice else "warn",
            f"{len(stun)} STUN, {len(turn)} TURN: {', '.join(u.split('?')[0] for u in ice) or 'none'}",
            "" if ice else "host candidates only: fine on a LAN; remote teleop needs STUN/TURN",
        )
    ]
    target = next((t for t in map(stun_target, stun) if t), None)
    if target is None:
        out.append(Check("rtc:stun", "warn", "no stun: server configured", "add stun:host:3478"))
    elif os.environ.get("AUOREO_OFFLINE"):
        out.append(Check("rtc:stun", "ok", f"skipped (AUOREO_OFFLINE): {target[0]}:{target[1]}"))
    else:
        try:
            mapped = await asyncio.to_thread(stun_binding, *target)
            out.append(Check("rtc:stun", "ok", f"{target[0]}:{target[1]} → public {mapped}"))
        except (OSError, ValueError) as exc:
            out.append(
                Check(
                    "rtc:stun",
                    "warn",
                    f"{target[0]}:{target[1]} no answer in {STUN_TIMEOUT_S:.0f} s "
                    f"({type(exc).__name__}; offline or UDP blocked?)",
                    "check UDP egress; on CGNAT/symmetric NAT add a TURN server (turns: on 443)",
                )
            )
    out.append(
        Check(
            "rtc:turn",
            "ok" if turn else "warn",
            f"{len(turn)} TURN server(s)" if turn else "no TURN server",
            "" if turn else "peers behind CGNAT/symmetric NAT need TURN or the livekit transport",
        )
    )
    try:
        h = Harness(default_roots(st.home, Path.cwd(), bundled_root(), st.extra_roots))
        bdef = h.select_backend("rtc_transport", st.backends.get("rtc_transport"))
        if bdef is None:
            out.append(Check("rtc:transport", "warn", "none selected", "set settings.backends.rtc_transport"))
            return out
        res = await asyncio.wait_for(bdef.load()(st).check(), 10)
        level: Level = "ok" if res.ok else "warn"
        out.append(Check("rtc:transport", level, f"{bdef.manifest.name}: {res.detail}", res.remedy))
    except (HarnessError, KeyError) as exc:
        out.append(Check("rtc:transport", "warn", str(exc), "fix settings.backends.rtc_transport"))
    return out
