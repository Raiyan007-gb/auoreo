"""`POST /acp` — Agent Client Protocol over JSON-RPC 2.0 (spec §5.4, §2.11, L1039).

Single requests or batches (≤ `MAX_BATCH`). Methods: `initialize`, `authenticate`,
`session/new`, `session/load`, `session/prompt`, `session/cancel`, `session/set_mode`.
Wire names are camelCase, paths absolute, lines 1-based. `session/prompt` runs one
non-interactive turn (tools that need approval are denied) and answers with its
`stopReason` once the turn ends; the final text is echoed in `_meta.auoreo.text`.

**Streaming** (F3): a single `session/prompt` request sent with `Accept: text/event-stream`
answers as SSE: `session/update` notifications (`agent_message_chunk` from `message.delta`,
`tool_call` + `tool_call_update` pending → in_progress → completed|failed from `tool.*`,
`plan` from `plan.updated` / `todo.updated`) as `data:` lines, then the JSON-RPC response
for the request, then the stream closes. AUOREO has no thinking events, so no
`agent_thought_chunk` is sent. Without that Accept header nothing changes; the durable
`GET /api/events?session_id=…` stream carries the same events either way.

`session/set_mode` takes `modeId` = `manual` | `acceptEdits` | `plan` (the permission mode
for that session's following turns; `session/new` lists them under `modes`).
`session/cancel` is advisory and idempotent: unknown or idle sessions are never an error.
`session/new` writes the protocol-version/capability handshake (the client's last
`initialize` and any `ACP-Protocol-Version` header) to the audit log as `acp.handshake`.

Notifications (no `id`) get no response; a batch of only notifications answers 204.
Unknown `_`-prefixed extension methods are ignored when sent as notifications (requests get
-32601), counted in `stats()` and audited once per name; `initialize` advertises in
`_meta.auoreo` that no extension methods are implemented.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, fields
from pathlib import Path, PurePath
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import Response

from auoreo import __version__
from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.harness.orchestrator import floors
from auoreo.schema.events import ServerEvent

log = logging.getLogger(__name__)
router = APIRouter(tags=["compat"], dependencies=AUTH)

PROTOCOL_VERSION = 1
MAX_BATCH = 32
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700,
    -32600,
    -32601,
    -32602,
    -32603,
)
STOP_REASONS = {
    "completed": "end_turn",
    "max_steps": "max_turn_requests",
    "aborted": "cancelled",
    "blocked": "refusal",
}
_BODY_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"description": "JSON-RPC 2.0 request or batch"}}},
    }
}

Params = dict[str, Any]
Handler = Callable[[Services, Params], Awaitable[Any]]
MODES = {
    "manual": "Ask before edits and commands (non-interactive here: such calls are denied)",
    "acceptEdits": "Allow file edits without asking; other approvals are still denied",
    "plan": "Read-only planning: edits are blocked outside the plans directory",
}
KEEPALIVE_S = 15.0
_HEADERS: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar("acp_headers", default={})  # noqa: B039
_STATE: dict[str, Any] = {"initialize": None, "audited_ext": set()}
_STATS: Counter[str] = Counter()
_EXT: Counter[str] = Counter()


def stats() -> dict[str, Any]:
    """Process-local counters: requests, streams, ignored `_` extension methods by name."""
    return {**_STATS, "ext_ignored": sum(_EXT.values()), "ext_methods": dict(_EXT)}


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def _error(rid: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": rid, "error": err}


@router.post(
    "/acp",
    openapi_extra=_BODY_SCHEMA,
    responses={
        200: {
            "description": "JSON-RPC response or batch; SSE of session/update + the response when streaming",
            "content": {"text/event-stream": {}},
        },
        204: {"description": "Only notifications"},
    },
)
async def acp(request: Request, svc: ServicesDep) -> Response:
    """JSON-RPC 2.0 entry point for ACP clients (editors). `Accept: text/event-stream` on a
    single `session/prompt` streams `session/update` notifications before the response."""
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(_error(None, PARSE_ERROR, "Parse error"))
    _HEADERS.set({k: v for k, v in request.headers.items() if k.startswith("acp-")})
    if _wants_stream(request, payload):
        _STATS["streams"] += 1
        sse = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        return StreamingResponse(stream_prompt(svc, payload), media_type="text/event-stream", headers=sse)
    if isinstance(payload, list):
        if not payload:
            return JSONResponse(_error(None, INVALID_REQUEST, "empty batch"))
        if len(payload) > MAX_BATCH:
            return JSONResponse(_error(None, INVALID_REQUEST, f"batch larger than {MAX_BATCH}"))
        results = await asyncio.gather(*(dispatch(svc, m) for m in payload))
        out = [r for r in results if r is not None]
        return JSONResponse(out) if out else Response(status_code=204)
    single = await dispatch(svc, payload)
    return JSONResponse(single) if single is not None else Response(status_code=204)


async def dispatch(svc: Services, msg: Any) -> dict[str, Any] | None:
    """One JSON-RPC message → its response, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        rid = msg.get("id") if isinstance(msg, dict) else None
        return _error(rid, INVALID_REQUEST, "Invalid Request")
    is_request = "id" in msg
    rid, method = msg.get("id"), msg["method"]
    params = msg.get("params") or {}
    handler = METHODS.get(method)
    _STATS["requests" if is_request else "notifications"] += 1
    if handler is None:
        if method.startswith("_"):
            await _ext_ignored(svc, method)
        if not is_request:
            return None  # unknown notifications (incl. `_`-extensions) are ignored
        return _error(rid, METHOD_NOT_FOUND, f"Method not found: {method}")
    try:
        if not isinstance(params, dict):
            raise RpcError(INVALID_PARAMS, "params must be an object")
        result = await handler(svc, params)
    except RpcError as exc:
        return _error(rid, exc.code, exc.message, exc.data) if is_request else None
    except Exception as exc:  # never let one message take the batch down
        log.exception("acp %s failed", method)
        return _error(rid, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}") if is_request else None
    return {"jsonrpc": "2.0", "id": rid, "result": result} if is_request else None


# ------------------------------------------------------------------- methods
async def initialize(svc: Services, params: Params) -> Any:
    _STATE["initialize"] = {
        "protocolVersion": params.get("protocolVersion"),
        "clientCapabilities": params.get("clientCapabilities") or {},
        "clientInfo": params.get("clientInfo") or {},
    }
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "agentCapabilities": {
            "loadSession": True,
            "promptCapabilities": {"image": False, "audio": False, "embeddedContext": True},
        },
        "authMethods": [],
        "agentInfo": {"name": "auoreo", "version": __version__},
        "_meta": {"auoreo": {"extMethods": [], "unknownExtMethods": "ignored", "streaming": True}},
    }


async def authenticate(svc: Services, params: Params) -> Any:
    return {}  # no auth methods: the HTTP Bearer token already authenticated the client


async def session_new(svc: Services, params: Params) -> Any:
    warnings: list[str] = []
    cwd = params.get("cwd")
    if cwd is not None:
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            raise RpcError(INVALID_PARAMS, "cwd must be an absolute path")
        if PurePath(cwd) != svc.cwd:
            warnings.append(f"cwd {cwd} differs from the server's; tools run in {svc.cwd}")
    if params.get("mcpServers"):
        warnings.append("mcpServers are ignored; configure MCP servers in mcp/<name>.yaml")
    row = await svc.create_session(title="acp")
    await svc.audit.record(
        actor="acp",
        action="acp.session.new",
        session_id=row.id,
        detail={"protocolVersion": PROTOCOL_VERSION, "warnings": len(warnings)},
    )
    await _audit_handshake(svc, row.id)
    current = svc.settings.permission_mode if svc.settings.permission_mode in MODES else "manual"
    out: dict[str, Any] = {
        "sessionId": row.id,
        "modes": {
            "currentModeId": current,
            "availableModes": [{"id": k, "name": k, "description": v} for k, v in MODES.items()],
        },
    }
    if warnings:
        out["_meta"] = {"auoreo": {"warnings": warnings}}
    return out


async def _audit_handshake(svc: Services, sid: str) -> None:
    init = _STATE["initialize"] or {}
    await svc.audit.record(
        actor="acp",
        action="acp.handshake",
        session_id=sid,
        detail={
            "agentProtocolVersion": PROTOCOL_VERSION,
            "clientProtocolVersion": init.get("protocolVersion"),
            "headerVersion": _HEADERS.get().get("acp-protocol-version"),
            "clientCapabilities": sorted(init.get("clientCapabilities", {})),
            "clientInfo": {k: str(v)[:100] for k, v in init.get("clientInfo", {}).items()},
            "initialized": bool(init),
        },
    )


async def session_load(svc: Services, params: Params) -> Any:
    await _session(svc, params)
    return {}


async def session_prompt(svc: Services, params: Params) -> Any:
    sid = await _session(svc, params)
    text = prompt_text(params.get("prompt"))
    run_id = await svc.coordinator.submit(sid, text, interactive=False)
    if run_id is None:  # an engine-handled slash command (e.g. /model)
        return {"stopReason": "end_turn"}
    await svc.coordinator.wait(sid)
    result = svc.coordinator.result(run_id)
    if result is None:
        return {"stopReason": "cancelled", "_meta": {"auoreo": {"runId": run_id}}}
    if result.status == "failed":
        raise RpcError(INTERNAL_ERROR, "turn failed", {"runId": run_id, "error": result.error})
    meta = {"runId": run_id, "status": result.status, "text": result.text}
    return {"stopReason": STOP_REASONS.get(result.status, "end_turn"), "_meta": {"auoreo": meta}}


async def session_cancel(svc: Services, params: Params) -> Any:
    sid = params.get("sessionId")
    if isinstance(sid, str):
        await svc.coordinator.cancel(sid)
    return None


@dataclass
class AcpModeEngine(PermissionEngine):
    """Keeps an ACP-chosen permission mode across turns: the runtime refreshes the session
    engine's mode at each turn start and then re-runs `__post_init__`, which re-applies it."""

    acp_mode: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.acp_mode is not None:
            self.mode = self.acp_mode  # type: ignore[assignment]


async def session_set_mode(svc: Services, params: Params) -> Any:
    sid = await _session(svc, params)
    mode = params.get("modeId")
    if not isinstance(mode, str) or mode not in MODES:
        raise RpcError(INVALID_PARAMS, f"unknown modeId {mode!r}", {"available": list(MODES)})
    rt = await svc.coordinator.runtime(sid)
    rt.state.plan_mode = mode == "plan"
    if isinstance(rt.engine, AcpModeEngine):
        rt.engine.acp_mode = mode
        rt.engine.__post_init__()
    elif rt.engine is not None:
        base = {f.name: getattr(rt.engine, f.name) for f in fields(PermissionEngine)}
        rt.engine = AcpModeEngine(**base, acp_mode=mode)
    else:
        plans = str(svc.cwd / ".auoreo" / "plans")
        rt.engine = AcpModeEngine([], floors(svc.harness), "manual", plans, acp_mode=mode)
    await svc.audit.record(
        actor="acp", action="acp.session.set_mode", session_id=sid, detail={"modeId": mode}
    )
    return {}


METHODS: dict[str, Handler] = {
    "initialize": initialize,
    "authenticate": authenticate,
    "session/new": session_new,
    "session/load": session_load,
    "session/prompt": session_prompt,
    "session/cancel": session_cancel,
    "session/set_mode": session_set_mode,
}


async def _ext_ignored(svc: Services, method: str) -> None:
    _EXT[method[:64]] += 1
    seen: set[str] = _STATE["audited_ext"]
    if method not in seen and len(seen) < 64:
        seen.add(method)
        await svc.audit.record(actor="acp", action="acp.ext.ignored", detail={"method": method[:64]})


async def _session(svc: Services, params: Params) -> str:
    sid = params.get("sessionId")
    if not isinstance(sid, str) or await svc.get_session(sid) is None:
        raise RpcError(INVALID_PARAMS, f"unknown session {sid!r}")
    return sid


def _block_text(block: Any) -> str:
    if not isinstance(block, dict):
        raise RpcError(INVALID_PARAMS, "prompt blocks must be objects")
    kind = block.get("type")
    if kind == "text":
        return str(block.get("text", ""))
    if kind == "resource":
        res = block.get("resource") or {}
        if not isinstance(res, dict) or "text" not in res:
            raise RpcError(INVALID_PARAMS, "only text resources are supported")
        return f'<resource uri="{res.get("uri", "")}">\n{res["text"]}\n</resource>'
    if kind == "resource_link":
        return f"[resource: {block.get('uri', '')}]"
    raise RpcError(INVALID_PARAMS, f"unsupported prompt block type {kind!r}")


def prompt_text(prompt: Any) -> str:
    if not isinstance(prompt, list) or not prompt:
        raise RpcError(INVALID_PARAMS, "prompt must be a non-empty list of content blocks")
    text = "\n\n".join(t for t in (_block_text(b) for b in prompt) if t)
    if not text.strip():
        raise RpcError(INVALID_PARAMS, "prompt is empty")
    return text


# ----------------------------------------------------------------- streaming
def _wants_stream(request: Request, payload: Any) -> bool:
    return (
        "text/event-stream" in request.headers.get("accept", "")
        and isinstance(payload, dict)
        and payload.get("method") == "session/prompt"
        and "id" in payload
    )


def _sse(obj: Any) -> str:
    return "data: " + json.dumps(obj, separators=(",", ":"), default=str) + "\n\n"


async def stream_prompt(svc: Services, msg: dict[str, Any]) -> AsyncIterator[str]:
    """SSE body: `session/update` notifications for the turn, then the JSON-RPC response."""
    params = msg.get("params")
    sid = params.get("sessionId") if isinstance(params, dict) else None
    sub = svc.bus.subscribe(frozenset({f"session:{sid}"})) if isinstance(sid, str) else None
    task = asyncio.create_task(dispatch(svc, msg))  # subscribed first: nothing falls in the gap
    try:
        while sub is not None:
            getter = asyncio.ensure_future(sub.queue.get())
            done, _ = await asyncio.wait({getter, task}, timeout=KEEPALIVE_S, return_when="FIRST_COMPLETED")
            if getter in done:
                for note in session_updates(str(sid), getter.result()):
                    yield _sse(note)
                continue
            getter.cancel()
            if not done:
                yield ": keepalive\n\n"
                continue
            while not sub.queue.empty():  # the turn is over: flush what it emitted
                for note in session_updates(str(sid), sub.queue.get_nowait()):
                    yield _sse(note)
            break
        yield _sse(await task)
    finally:
        if sub is not None:
            svc.bus.unsubscribe(sub)


TOOL_KINDS = {
    "read": "read",
    "edit": "edit",
    "write": "edit",
    "multi_edit": "edit",
    "apply_patch": "edit",
    "bash": "execute",
    "grep": "search",
    "glob": "search",
    "web_fetch": "fetch",
    "web_search": "fetch",
}


def _raw_input(arguments: Any) -> Any:
    try:
        return json.loads(arguments) if isinstance(arguments, str) else arguments
    except json.JSONDecodeError:
        return {"truncated": arguments}


def _message_chunk(d: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": d.get("text", "")}}]


def _tool_started(d: dict[str, Any]) -> list[dict[str, Any]]:
    tool, cid = str(d.get("tool", "")), d.get("call_id")
    call = {"sessionUpdate": "tool_call", "toolCallId": cid, "title": tool, "status": "pending"}
    call |= {"kind": TOOL_KINDS.get(tool, "other"), "rawInput": _raw_input(d.get("arguments"))}
    return [call, {"sessionUpdate": "tool_call_update", "toolCallId": cid, "status": "in_progress"}]


def _tool_completed(d: dict[str, Any]) -> list[dict[str, Any]]:
    text = {"type": "content", "content": {"type": "text", "text": str(d.get("content", ""))}}
    status = "failed" if d.get("is_error") else "completed"
    return [
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": d.get("call_id"),
            "status": status,
            "content": [text],
        }
    ]


def _plan(d: dict[str, Any]) -> list[dict[str, Any]]:
    items = d.get("steps") or d.get("todos") or []
    entries = [
        {
            "content": str(i.get("step") or i.get("content") or ""),
            "priority": str(i.get("priority") or "medium"),
            "status": str(i.get("status") or "pending"),
        }
        for i in items
        if isinstance(i, dict)
    ]
    return [{"sessionUpdate": "plan", "entries": entries}]


UPDATES: dict[str, Callable[[dict[str, Any]], list[dict[str, Any]]]] = {
    "message.delta": _message_chunk,
    "tool.started": _tool_started,
    "tool.completed": _tool_completed,
    "plan.updated": _plan,
    "todo.updated": _plan,
}


def session_updates(sid: str, event: ServerEvent) -> list[dict[str, Any]]:
    """One AUOREO event → zero or more ACP `session/update` notifications."""
    fn = UPDATES.get(event.type)
    if fn is None:
        return []
    return [
        {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}}
        for u in fn(event.data)
    ]
