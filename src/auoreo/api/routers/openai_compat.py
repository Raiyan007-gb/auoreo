"""OpenAI-compatible `/v1` surface (spec §5.4): a thin translate → gateway adapter.

`POST /v1/chat/completions` turns an OpenAI request into an `LLMRequest` (with the
`prompts/variants.yaml` shaping the agent loop uses) and calls `Gateway.stream` directly.
There is no agent loop here and **no server-side tool execution**: `tools` are the
client's, tool calls are returned to the client in OpenAI shape and it sends the results
back as `role: "tool"` messages. Every call is audited (`compat.openai`: model, tokens,
finish reason — never message bodies), recorded in the usage ledger under session
`compat`, and the response text is redacted like any other model output.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import Response

from auoreo.api.deps import AUTH, ServicesDep
from auoreo.core.redaction import StreamRedactor, redact
from auoreo.core.services import Services
from auoreo.gateway.errors import GatewayError
from auoreo.gateway.types import Finish, LLMRequest, StreamItem, TextDelta, ToolCall, Usage
from auoreo.harness.orchestrator import pick_variant
from auoreo.schema.compat import (
    OpenAIChatCompletion,
    OpenAIChatRequest,
    OpenAIChoice,
    OpenAIMessage,
    OpenAIModel,
    OpenAIModelList,
    OpenAIToolCall,
    OpenAIToolCallFunction,
    OpenAIUsage,
)

router = APIRouter(prefix="/v1", tags=["compat"], dependencies=AUTH)

MODEL_PREFIX = "auoreo/"
"""`auoreo/<group>` is accepted (and listed) as an alias of routing group `<group>`."""
COMPAT_SESSION = "compat"
ERROR_STATUS: dict[str, tuple[int, str]] = {
    "AUTH": (401, "authentication_error"),
    "RATE_LIMIT": (429, "rate_limit_error"),
    "QUOTA": (429, "insufficient_quota"),
    "CONTEXT": (400, "invalid_request_error"),
    "INVALID_REQUEST": (400, "invalid_request_error"),
    "CONTENT_POLICY": (400, "invalid_request_error"),
    "PAYLOAD_TOO_LARGE": (413, "invalid_request_error"),
    "MODEL_NOT_FOUND": (404, "invalid_request_error"),
    "BUDGET": (402, "insufficient_quota"),
    "BILLING": (402, "insufficient_quota"),
}
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"description": "OpenAI error envelope `{error: {message, type, code}}`"}
    for code in (400, 402, 404, 429, 502)
}


def error_body(err: GatewayError) -> tuple[int, dict[str, Any]]:
    status, kind = ERROR_STATUS.get(err.code, (502, "api_error"))
    return status, {"error": {"message": err.message, "type": kind, "code": err.code.lower()}}


def resolve(svc: Services, model: str) -> str:
    raw = model.removeprefix(MODEL_PREFIX)
    return svc.settings.aliases.get(raw, raw)


def to_request(svc: Services, body: OpenAIChatRequest, completion_id: str) -> LLMRequest:
    model = resolve(svc, body.model)
    variant = pick_variant(svc.harness, model)
    temperature = body.temperature
    if temperature is None and variant is not None:
        temperature = variant.temperature
    # LLMRequest knows auto|none; "required"/a named function degrade to "auto" (documented).
    tool_choice: Literal["auto", "none"] = "none" if body.tool_choice == "none" else "auto"
    return LLMRequest(
        model=model,
        messages=tuple(body.messages),
        tools=tuple(body.tools or ()),
        tool_choice=tool_choice,
        temperature=temperature,
        max_tokens=body.max_tokens or body.max_completion_tokens,
        omit_fields=tuple(variant.omit_fields) if variant else (),
        metadata={"purpose": "compat", "run_id": completion_id, **({"user": body.user} if body.user else {})},
    )


@router.get("/models", response_model=OpenAIModelList)
async def list_models(svc: ServicesDep) -> OpenAIModelList:
    """Routing groups from `settings.models`, their `auoreo/<group>` forms, and aliases."""
    groups = list(svc.settings.models)
    ids = [*groups, *(MODEL_PREFIX + g for g in groups), *svc.settings.aliases]
    return OpenAIModelList(data=[OpenAIModel(id=i) for i in dict.fromkeys(ids)])


@router.post(
    "/chat/completions",
    response_model=OpenAIChatCompletion,
    responses={200: {"content": {"text/event-stream": {}}}, **ERROR_RESPONSES},
)
async def chat_completions(body: OpenAIChatRequest, svc: ServicesDep) -> Any:
    """Non-streaming → a `chat.completion` object; `stream: true` → SSE `chat.completion.chunk`
    lines and `data: [DONE]`. Tool calls are returned, never executed."""
    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    request = to_request(svc, body, cid)
    stream = svc.gateway.stream(request).__aiter__()
    try:  # pull the first item so an upfront gateway error becomes a proper HTTP status
        first: StreamItem | None = await anext(stream)
    except StopAsyncIteration:
        first = None
    except GatewayError as err:
        await _audit(svc, request, body, None, "error", err.code)
        status, payload = error_body(err)
        return JSONResponse(payload, status_code=status)
    call = _Call(svc, request, body, cid, first, stream)
    if body.stream:
        include_usage = bool((body.stream_options or {}).get("include_usage"))
        return StreamingResponse(
            call.sse(include_usage),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return await call.complete()


class _Call:
    def __init__(
        self,
        svc: Services,
        request: LLMRequest,
        body: OpenAIChatRequest,
        cid: str,
        first: StreamItem | None,
        stream: AsyncIterator[StreamItem],
    ) -> None:
        self.svc, self.request, self.body, self.cid = svc, request, body, cid
        self.first, self.stream = first, stream
        self.created = int(time.time())
        self.usage = Usage()
        self.finish = "stop"
        self.scrub: StreamRedactor | None = StreamRedactor() if svc.settings.redact_secrets else None

    async def items(self) -> AsyncIterator[StreamItem]:
        if self.first is None:
            return
        yield self.first
        async for item in self.stream:
            yield item

    async def done(self, error: str | None = None) -> None:
        if error is None:
            await self.svc.coordinator.runner.usage.record(
                COMPAT_SESSION, self.cid, self.request.model, self.usage
            )
        await _audit(self.svc, self.request, self.body, self.usage, self.finish, error)

    def text(self, chunk: str) -> str:
        return chunk if self.scrub is None else self.scrub.feed(chunk)

    def flush(self) -> str:
        return "" if self.scrub is None else self.scrub.flush()

    async def complete(self) -> Response | OpenAIChatCompletion:
        parts: list[str] = []
        calls: list[ToolCall] = []
        try:
            async for item in self.items():
                match item:
                    case TextDelta(text=t):
                        parts.append(t)
                    case ToolCall():
                        calls.append(item)
                    case Usage():
                        self.usage = item
                    case Finish(reason=r):
                        self.finish = r
        except GatewayError as err:
            await self.done(err.code)
            status, payload = error_body(err)
            return JSONResponse(payload, status_code=status)
        await self.done()
        text = "".join(parts)
        if self.scrub is not None:
            text = redact(text)
        tool_calls = [
            OpenAIToolCall(id=c.id, function=OpenAIToolCallFunction(name=c.name, arguments=c.arguments))
            for c in calls
        ]
        return OpenAIChatCompletion(
            id=self.cid,
            created=self.created,
            model=self.body.model,
            choices=[
                OpenAIChoice(
                    message=OpenAIMessage(content=text or None, tool_calls=tool_calls or None),
                    finish_reason=self.finish,
                )
            ],
            usage=_usage(self.usage),
        )

    def chunk(self, delta: dict[str, Any], finish: str | None, include_usage: bool) -> str:
        obj: dict[str, Any] = {
            "id": self.cid,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.body.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if include_usage:
            obj["usage"] = None
        return f"data: {json.dumps(obj)}\n\n"

    async def sse(self, include_usage: bool) -> AsyncIterator[str]:
        yield self.chunk({"role": "assistant", "content": ""}, None, include_usage)
        index = 0
        try:
            async for item in self.items():
                match item:
                    case TextDelta(text=t):
                        if safe := self.text(t):
                            yield self.chunk({"content": safe}, None, include_usage)
                    case ToolCall(id=call_id, name=name, arguments=args):
                        if tail := self.flush():
                            yield self.chunk({"content": tail}, None, include_usage)
                        fn = {"name": name, "arguments": args}
                        tc = {"index": index, "id": call_id, "type": "function", "function": fn}
                        index += 1
                        yield self.chunk({"tool_calls": [tc]}, None, include_usage)
                    case Usage():
                        self.usage = item
                    case Finish(reason=r):
                        self.finish = r
        except GatewayError as err:  # mid-stream: report in-band, then terminate the stream
            await self.done(err.code)
            yield f"data: {json.dumps(error_body(err)[1])}\n\n"
            yield "data: [DONE]\n\n"
            return
        finally:
            aclose = getattr(self.stream, "aclose", None)
            if aclose is not None:
                await aclose()
        if tail := self.flush():
            yield self.chunk({"content": tail}, None, include_usage)
        yield self.chunk({}, self.finish, include_usage)
        await self.done()
        if include_usage:
            final = {
                "id": self.cid,
                "object": "chat.completion.chunk",
                "created": self.created,
                "model": self.body.model,
                "choices": [],
                "usage": _usage(self.usage).model_dump(),
            }
            yield f"data: {json.dumps(final)}\n\n"
        yield "data: [DONE]\n\n"


def _usage(u: Usage) -> OpenAIUsage:
    return OpenAIUsage(
        prompt_tokens=u.prompt_tokens,
        completion_tokens=u.completion_tokens,
        total_tokens=u.prompt_tokens + u.completion_tokens,
    )


async def _audit(
    svc: Services,
    request: LLMRequest,
    body: OpenAIChatRequest,
    usage: Usage | None,
    finish: str,
    error: str | None,
) -> None:
    detail: dict[str, Any] = {
        "model": request.model,
        "requested_model": body.model,
        "stream": body.stream,
        "messages": len(body.messages),
        "tools": len(body.tools or ()),
        "finish_reason": finish,
        "prompt_tokens": usage.prompt_tokens if usage else 0,
        "completion_tokens": usage.completion_tokens if usage else 0,
    }
    if error:
        detail["error"] = error
    await svc.audit.record(
        actor="compat",
        action="compat.openai",
        session_id=COMPAT_SESSION,
        decision="error" if error else "allow",
        detail=detail,
    )
