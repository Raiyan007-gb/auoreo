"""I9: uvicorn WebSocket implementation + protocol-ping defaults (spec §25.12 L1037).

- the implementation is pinned (`i9.ws.impl`, default `websockets-sansio`; `wsproto` is
  dropped from the default, `auto` is never used so an extra install cannot switch it);
- on a loopback bind protocol pings are off (`ws_ping_interval=None`) unless
  `i9.ws.loopback_ping` (or an explicit `--ws-ping-*` flag): the app-level `ping{tick}` frame
  (`ws_ping_interval_s`, ≤ 30 s) is authoritative; uvicorn's ping only detects half-open
  sockets, which a loopback-only server rarely has (an SSH tunnel is the carve-out: enable
  `loopback_ping` there);
- beyond loopback: 20 s / 20 s from settings.

`auoreo doctor` (`diagnostics_i9`) asserts the implementation is importable and prints the
resolved ping settings.
"""

from __future__ import annotations

import importlib
from typing import Any

from auoreo.core.settings import Settings

APP_PING_MAX_S = 30.0


def ws_kwargs(st: Settings) -> dict[str, Any]:
    cfg = st.i9.ws
    out: dict[str, Any] = {"ws": cfg.impl}
    if st.is_loopback and not cfg.loopback_ping:
        out.update(ws_ping_interval=None, ws_ping_timeout=None)
    else:
        out.update(ws_ping_interval=cfg.ping_interval_s, ws_ping_timeout=cfg.ping_timeout_s)
    return out


def explicit_ping(st: Settings, interval: float | None, timeout: float | None) -> None:
    """`--ws-ping-interval/--ws-ping-timeout` given: protocol pings on, with those values."""
    if interval is None and timeout is None:
        return
    st.i9.ws.loopback_ping = True
    if interval is not None:
        st.i9.ws.ping_interval_s = interval
    if timeout is not None:
        st.i9.ws.ping_timeout_s = timeout


def impl_available(name: str) -> tuple[bool, str]:
    """Whether uvicorn can load WebSocket implementation `name` here."""
    try:
        from uvicorn.config import WS_PROTOCOLS
    except ImportError as exc:  # pragma: no cover - uvicorn is a hard dependency
        return False, str(exc)
    ref = WS_PROTOCOLS.get(name)
    if not ref:
        return False, f"uvicorn has no '{name}' implementation (known: {sorted(WS_PROTOCOLS)})"
    module = ref.split(":", 1)[0]
    try:
        importlib.import_module(module)
    except ImportError as exc:
        return False, f"{module}: {exc}"
    return True, ref
