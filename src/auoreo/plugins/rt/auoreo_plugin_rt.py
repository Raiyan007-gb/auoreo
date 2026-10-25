"""AUOREO plugin runtime: JSON-RPC 2.0 over stdio, one JSON object per line.

Standard library only: this file is copied into each plugin venv's site-packages and run as
`python -m auoreo_plugin_rt <entry> --dir <plugin folder>`, or, for creator mode, as
`python -I auoreo_plugin_rt.py --ephemeral <file.py> --name <tool>`.

A plugin class is constructed with a `HostAPI` and may define (all optional, sync or async):

    initialize(config, consumes_granted) -> {tools, deliveries, connectors, skills}
    call_tool(name, args, session_id) -> str | {"content": str, "is_error": bool}
    send(name, target, text, meta) -> {"ok": bool, "detail": str, "external_id": str | None}
    connector_start(name) / connector_stop(name)
    config_update(config)
    shutdown()

Class attributes `TOOLS`, `DELIVERIES`, `CONNECTORS`, `SKILLS` are used when there is no
`initialize`. Anything the plugin prints goes to stderr; stdout is reserved for the protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import inspect
import itertools
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = 1
KINDS = ("tools", "deliveries", "connectors", "skills")
HANDLERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "tool.call": ("call_tool", ("name", "args", "session_id")),
    "delivery.send": ("send", ("name", "target", "text", "meta")),
    "connector.start": ("connector_start", ("name",)),
    "connector.stop": ("connector_stop", ("name",)),
    "config.update": ("config_update", ("config",)),
}
DEFAULTS: dict[str, Any] = {"args": {}, "meta": {}, "session_id": "", "config": {}}


async def _maybe(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class HostAPI:
    """What a plugin may ask of the host. Every call is checked host side."""

    def __init__(self, rt: Runtime) -> None:
        self._rt = rt
        self.name = os.environ.get("AUOREO_PLUGIN", "")
        self.config: dict[str, Any] = {}

    async def secret(self, name: str) -> str | None:
        """Resolve `secrets:<name>`; only names declared in `consumes` are answered."""
        res = await self._rt.request("secret.get", {"name": name})
        return res.get("value") if isinstance(res, dict) else None

    async def pairing_request(self, connector: str, chat_id: str, user: str) -> dict[str, Any]:
        """Is `chat_id` allowed? Unknown chats get `{allowed: false, code: "123456"}`."""
        params = {"name": connector, "chat_id": str(chat_id), "user": user}
        res = await self._rt.request("pairing.request", params)
        return res if isinstance(res, dict) else {}

    def message(self, connector: str, chat_id: str, user: str, text: str, reply_to: Any = None) -> None:
        """Inbound message: the host runs it in the session keyed (plugin, chat_id)."""
        params = {
            "name": connector,
            "chat_id": str(chat_id),
            "user": user,
            "text": text,
            "reply_to": reply_to,
        }
        self._rt.notify("connector.message", params)

    def log(self, level: str, message: str) -> None:
        self._rt.notify("log", {"level": level, "message": message})

    async def audit(self, action: str, detail: dict[str, Any], decision: str = "") -> None:
        """Append an audit row `plugin.<name>.<action>` (detail redacted, ≤ 4 KiB)."""
        await self._rt.request("audit.record", {"action": action, "detail": detail, "decision": decision})

    async def usage(
        self, session_id: str | None, model: str, cost_usd: float, *, estimated: bool = True
    ) -> None:
        """Record spend in the host's usage ledger; needs `consumes: [usage]`."""
        params = {"session_id": session_id, "model": model, "cost_usd": cost_usd, "estimated": estimated}
        await self._rt.request("usage.record", params)


def _load_attr(path: Path, attr: str) -> Any:
    spec = importlib.util.spec_from_file_location(f"auoreo_plugin_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return getattr(module, attr)


class _Ephemeral:
    """Creator mode: one `async def run(args, ctx)` loaded from a file."""

    def __init__(self, host: HostAPI, path: Path, name: str) -> None:
        self.host = host
        self.name = name
        self.fn = _load_attr(path, "run")

    def initialize(self, config: dict[str, Any], consumes_granted: list[str]) -> dict[str, Any]:
        params = config.get("parameters") or {"type": "object", "properties": {}}
        desc = config.get("description") or self.name
        return {"tools": [{"name": self.name, "description": desc, "parameters": params}]}

    async def call_tool(self, name: str, args: dict[str, Any], session_id: str) -> Any:
        ctx = argparse.Namespace(session_id=session_id, log=lambda m: self.host.log("info", str(m)))
        return await _maybe(self.fn(args, ctx))


def _shape(method: str, result: Any) -> Any:
    if method == "tool.call":
        if isinstance(result, dict) and "content" in result:
            return {"content": str(result["content"]), "is_error": bool(result.get("is_error"))}
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        return {"content": text, "is_error": False}
    if method == "delivery.send":
        res = result if isinstance(result, dict) else {"ok": bool(result)}
        return {
            "ok": bool(res.get("ok")),
            "detail": str(res.get("detail", "")),
            "external_id": res.get("external_id"),
        }
    return result if isinstance(result, dict) else {}


class Runtime:
    def __init__(self, out: Any) -> None:
        self._out = out
        self._ids = itertools.count(1)
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self.plugin: Any = None
        self.host = HostAPI(self)
        self.stopping = asyncio.Event()

    # ----------------------------------------------------------------- wire
    def _write(self, obj: dict[str, Any]) -> None:
        self._out.write((json.dumps(obj, default=str) + "\n").encode())
        self._out.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def request(self, method: str, params: dict[str, Any], timeout: float = 30.0) -> Any:
        rid = f"p{next(self._ids)}"
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    # ------------------------------------------------------------- dispatch
    async def _initialize(self, p: dict[str, Any]) -> dict[str, Any]:
        self.host.config = dict(p.get("config") or {})
        init = getattr(self.plugin, "initialize", None)
        if init is not None:
            info = await _maybe(init(self.host.config, list(p.get("consumes_granted") or [])))
        else:
            info = {k: list(getattr(self.plugin, k.upper(), [])) for k in KINDS}
        return {"protocol": PROTOCOL_VERSION, **{k: [] for k in KINDS}, **(info or {})}

    async def _call(self, method: str, p: dict[str, Any]) -> Any:
        if method == "initialize":
            return await self._initialize(p)
        if method == "shutdown":
            fn = getattr(self.plugin, "shutdown", None)
            if fn is not None:
                await _maybe(fn())
            self.stopping.set()
            return {}
        if method not in HANDLERS:
            raise RpcError(-32601, f"method not found: {method}")
        attr, keys = HANDLERS[method]
        fn = getattr(self.plugin, attr, None)
        if fn is None:
            raise RpcError(-32601, f"plugin does not implement {attr}")
        result = await _maybe(fn(*(p.get(k, DEFAULTS.get(k)) for k in keys)))
        return _shape(method, result)

    async def _handle(self, msg: dict[str, Any]) -> None:
        rid = msg.get("id")
        try:
            result = await self._call(str(msg.get("method")), msg.get("params") or {})
            reply: dict[str, Any] = {"result": result}
        except RpcError as exc:
            reply = {"error": {"code": exc.code, "message": str(exc)}}
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            reply = {"error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"}}
        if rid is not None:
            self._write({"jsonrpc": "2.0", "id": rid, **reply})

    def _dispatch(self, msg: dict[str, Any]) -> None:
        if "method" not in msg:  # a response to one of our requests
            fut = self._pending.get(str(msg.get("id")))
            if fut is None or fut.done():
                return
            err = msg.get("error")
            if err:
                fut.set_exception(RpcError(int(err.get("code", -32000)), str(err.get("message", ""))))
            else:
                fut.set_result(msg.get("result"))
            return
        task = asyncio.create_task(self._handle(msg))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def serve(self) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=32 * 1024 * 1024)
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        stop_task = asyncio.ensure_future(self.stopping.wait())
        while not self.stopping.is_set():
            line_task = asyncio.ensure_future(reader.readline())
            await asyncio.wait({line_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
            if not line_task.done():
                line_task.cancel()
                break
            line = line_task.result()
            if not line:
                break
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print(f"auoreo_plugin_rt: bad line {line[:200]!r}", file=sys.stderr)
                continue
            if isinstance(msg, dict):
                self._dispatch(msg)
        stop_task.cancel()
        if self._tasks:
            await asyncio.wait(list(self._tasks), timeout=5)
        stop = getattr(self.plugin, "shutdown", None)
        if stop is not None and not self.stopping.is_set():
            try:
                await _maybe(stop())
            except Exception:
                traceback.print_exc(file=sys.stderr)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="auoreo_plugin_rt")
    ap.add_argument("entry", nargs="?", default="plugin.py:Plugin")
    ap.add_argument("--dir", default=".")
    ap.add_argument("--ephemeral", default=None)
    ap.add_argument("--name", default="tool")
    ns = ap.parse_args(argv)
    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # plugin prints must never corrupt the protocol stream
    rt = Runtime(out)
    if ns.ephemeral:
        rt.plugin = _Ephemeral(rt.host, Path(ns.ephemeral), ns.name)
    else:
        folder = Path(ns.dir).resolve()
        sys.path.insert(0, str(folder))
        file_name, cls_name = ns.entry.split(":", 1)
        rt.plugin = _load_attr(folder / file_name, cls_name)(rt.host)
    asyncio.run(rt.serve())


if __name__ == "__main__":
    main()
