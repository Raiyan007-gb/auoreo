"""MCP client extras (spec §15 L664, §25.15 L1075, §26.14): `tools/list_changed`, `roots`, SSE.

- **list_changed**: a legacy (handshake-era) server pushes `notifications/tools/list_changed`
  on the connection (seen by the message handler); a 2026-07-28 server only publishes it on a
  `subscriptions/listen` stream, which `listen_tools_changed` keeps open. Either way the
  bridge re-lists the server's tools, re-registers them (bumping the harness generation) and
  emits `mcp.tools_changed`. Bursts are coalesced (`i6.mcp.refresh_debounce_s`).
- **roots** (deprecated by 2026-07-28 / SEP-2577; compat shim, `roots.enabled` per server):
  `[calling session's cwd, roots.paths…]` as `file://` URIs — each realpath'd, must be an
  existing directory (symlink escapes resolve to their target). When a tool call arrives
  from a different cwd the roots change and `notifications/roots/list_changed` is sent
  (legacy connections; modern servers ask again via `roots/list` in an input round).
- **SSE**: `transport: sse` → the SDK's legacy `sse_client` (forced `mode="legacy"`).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import warnings
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp.client.sse import sse_client
from mcp.client.subscriptions import ListenNotSupportedError
from mcp.types import ListRootsResult, Root

log = logging.getLogger(__name__)
TOOLS_CHANGED = "notifications/tools/list_changed"


def sse_target(url: str, headers: dict[str, str] | None = None) -> Any:
    return sse_client(url, headers=headers or None)


# ------------------------------------------------------------------ roots
def root_paths(cwd: Path | None, extra: list[str]) -> list[Path]:
    """Validated root directories: cwd first, then the explicit allow-list (deduplicated)."""
    out: list[Path] = []
    for raw in [str(cwd)] if cwd is not None else []:
        out.append(Path(raw))
    for raw in extra:
        try:
            out.append(Path(raw).expanduser())
        except RuntimeError:  # `~user` that does not exist
            continue
    seen: list[Path] = []
    for p in out:
        if not p.is_absolute():
            continue
        try:
            real = p.resolve(strict=True)
        except OSError:
            continue
        if real.is_dir() and real not in seen:
            seen.append(real)
    return seen


def root_uris(cwd: Path | None, extra: list[str]) -> tuple[str, ...]:
    return tuple(p.as_uri() for p in root_paths(cwd, extra))


class RootsState:
    """Per-connection roots: what the server was last told, and the cwd it derives from."""

    def __init__(self, extra: list[str]) -> None:
        self.extra = list(extra)
        self.cwd: Path | None = None
        self.sent: tuple[str, ...] | None = None

    def current(self) -> tuple[str, ...]:
        return root_uris(self.cwd, self.extra)

    async def callback(self, context: Any = None) -> ListRootsResult:
        uris = self.current()
        self.sent = uris
        return ListRootsResult(roots=[Root(uri=u, name=Path(u[7:]).name or "/") for u in uris])

    async def note_cwd(self, cwd: Path, client: Any) -> bool:
        """Record the calling session's cwd; True when `roots/list_changed` was sent."""
        self.cwd = cwd
        if self.sent is None or self.current() == self.sent:
            return False
        self.sent = self.current()
        with warnings.catch_warnings(), contextlib.suppress(Exception):
            warnings.simplefilter("ignore")
            await client.send_roots_list_changed()
            return True
        return False


# ------------------------------------------------------------------ tools/list_changed
def with_tools_changed(
    inner: Callable[[Any], Awaitable[None]], on_change: Callable[[], None]
) -> Callable[[Any], Awaitable[None]]:
    """Wrap a `message_handler` so a pushed `tools/list_changed` triggers `on_change`."""

    async def handle(message: Any) -> None:
        msg = getattr(message, "root", message)
        if getattr(msg, "method", None) == TOOLS_CHANGED:
            on_change()
        await inner(message)

    return handle


async def listen_tools_changed(client: Any, on_change: Callable[[], None]) -> None:
    """Hold a `subscriptions/listen{toolsListChanged}` stream open (2026-07-28 servers only)."""
    try:
        async with client.listen(tools_list_changed=True) as sub:
            async for _event in sub:
                on_change()
    except ListenNotSupportedError:
        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # a dropped stream is not fatal: the next reconnect re-listens
        log.info("MCP listen stream ended: %s", exc)


class Debouncer:
    """Coalesce `on_change` bursts into one awaited `refresh()` per quiet period."""

    def __init__(self, refresh: Callable[[], Awaitable[Any]], delay_s: float) -> None:
        self.refresh = refresh
        self.delay_s = delay_s
        self.task: asyncio.Task[None] | None = None

    def __call__(self) -> None:
        if self.task is not None and not self.task.done():
            return
        self.task = asyncio.get_running_loop().create_task(self._run(), name="mcp:tools-changed")

    async def _run(self) -> None:
        await asyncio.sleep(self.delay_s)
        try:
            await self.refresh()
        except Exception:
            log.exception("MCP tool re-list failed")

    def cancel(self) -> None:
        if self.task is not None:
            self.task.cancel()


__all__ = [
    "Debouncer",
    "RootsState",
    "TOOLS_CHANGED",
    "listen_tools_changed",
    "root_paths",
    "root_uris",
    "sse_target",
    "with_tools_changed",
]
