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

MAX_CAUSE_CHARS = 2048

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
        cause: str | None = None,
        overloaded: bool = False,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.provider = provider
        self.status = status
        self.retry_after_ms = retry_after_ms
        self.request_id = request_id
        self.cause = cause
        """Raw provider body (redacted, at most `MAX_CAUSE_CHARS`) for diagnostics."""
        self.overloaded = overloaded
        """HTTP 529 / `overloaded_error`: counts toward the foreground fallback streak."""

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE


class FallbackTriggeredError(GatewayError):
    """Raised after N consecutive overloaded (529) errors on a foreground turn: the gateway
    moves the turn to the next fallback group instead of retrying the same one (spec §10.4)."""

    def __init__(self, model: str, streak: int, last: GatewayError | None = None) -> None:
        super().__init__(
            "RATE_LIMIT",
            f"{model}: {streak} consecutive overloaded errors; falling back",
            provider=last.provider if last else None,
            status=last.status if last else 529,
            request_id=last.request_id if last else None,
            cause=last.cause if last else None,
            overloaded=True,
        )
        self.model = model
        self.streak = streak

    @property
    def retryable(self) -> bool:
        return False
