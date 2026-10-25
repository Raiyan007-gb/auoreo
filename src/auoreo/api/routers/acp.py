"""`POST /acp` — Agent Client Protocol over JSON-RPC 2.0 (spec §5.4, §2.11).

Single requests or batches (≤ `MAX_BATCH`). Methods: `initialize`, `authenticate`,
`session/new`, `session/load`, `session/prompt`, `session/cancel`. Wire names are camelCase,
paths absolute, lines 1-based. `session/prompt` runs one non-interactive turn (tools that
need approval are denied) and answers with its `stopReason` once the turn ends; the live
`session/update`-equivalent stream is the durable event stream (`GET /api/events?session_id=…`),
and the final text is echoed in `_meta.auoreo.text`. `session/cancel` is advisory and
idempotent: unknown or idle sessions are never an error.

Notifications (no `id`) get no response; a batch of only notifications answers 204.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePath
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from auoreo import __version__
from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.services import Services

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
        200: {"description": "JSON-RPC response or batch"},
        204: {"description": "Only notifications"},
    },
)
async def acp(request: Request, svc: ServicesDep) -> Response:
    """JSON-RPC 2.0 entry point for ACP clients (editors)."""
    try:
        payload = json.loads(await request.body())
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(_error(None, PARSE_ERROR, "Parse error"))
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
    if handler is None:
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
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "agentCapabilities": {
            "loadSession": True,
            "promptCapabilities": {"image": False, "audio": False, "embeddedContext": True},
        },
        "authMethods": [],
        "agentInfo": {"name": "auoreo", "version": __version__},
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
    out: dict[str, Any] = {"sessionId": row.id}
    if warnings:
        out["_meta"] = {"auoreo": {"warnings": warnings}}
    return out


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


METHODS: dict[str, Handler] = {
    "initialize": initialize,
    "authenticate": authenticate,
    "session/new": session_new,
    "session/load": session_load,
    "session/prompt": session_prompt,
    "session/cancel": session_cancel,
}


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
