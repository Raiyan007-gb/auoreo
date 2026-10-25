"""OpenAI-compatible `/v1` wire models (M5h compat surface)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OpenAIChatRequest(BaseModel):
    """`POST /v1/chat/completions` body. Unknown OpenAI fields (`n`, `top_p`, …) are
    accepted and ignored; tools are the client's — nothing is executed server-side."""

    model_config = ConfigDict(extra="allow")
    model: str = Field(min_length=1)
    messages: list[dict[str, Any]] = Field(min_length=1)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    max_completion_tokens: int | None = Field(default=None, ge=1)
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    user: str | None = None


class OpenAIModel(BaseModel):
    id: str
    object: Literal["model"] = "model"
    created: int = 0
    owned_by: str = "auoreo"


class OpenAIModelList(BaseModel):
    object: Literal["list"] = "list"
    data: list[OpenAIModel]


class OpenAIToolCallFunction(BaseModel):
    name: str
    arguments: str


class OpenAIToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: OpenAIToolCallFunction


class OpenAIMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str | None
    tool_calls: list[OpenAIToolCall] | None = None


class OpenAIChoice(BaseModel):
    index: int = 0
    message: OpenAIMessage
    finish_reason: str


class OpenAIUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class OpenAIChatCompletion(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[OpenAIChoice]
    usage: OpenAIUsage
