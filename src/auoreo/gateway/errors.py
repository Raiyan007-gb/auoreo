"""Typed gateway errors. Route on `code`, never parse `message` (spec §10.5)."""

from __future__ import annotations

from typing import Literal

ErrorCode = Literal[
    "AUTH",
    "RATE_LIMIT",
    "QUOTA",
    "BILLING",
    "PAYLOAD_TOO_LARGE",
    "CONTEXT",
    "INVALID_REQUEST",
    "CONTENT_POLICY",
    "MODEL_NOT_FOUND",
    "FORMAT",
    "SERVER",
    "TIMEOUT",
    "TRANSPORT",
    "EMPTY_RESPONSE",
    "ABORTED",
    "BUDGET",
]

RETRYABLE: frozenset[str] = frozenset({"EMPTY_RESPONSE", "RATE_LIMIT", "SERVER", "TIMEOUT", "TRANSPORT"})


class GatewayError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        provider: str | None = None,
        status: int | None = None,
        retry_after_ms: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.provider = provider
        self.status = status
        self.retry_after_ms = retry_after_ms
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE
