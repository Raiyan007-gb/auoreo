"""VCR cassette support for provider HTTP tests (spec §20.4 "recorded" tier).

pytest-recording + vcrpy. Cassettes live in `tests/fixtures/cassettes/<test module>/<test>.yaml`.
The record mode defaults to `none` (pytest-recording's default): a cassette-backed test
replays only and any unrecorded request fails, so CI never touches the network. Re-record
locally and explicitly with a real key in the env:

    COHERE_API_KEY=... uv run pytest tests/unit/test_cassette_providers.py --record-mode=once

Secrets never reach a cassette: credential headers and query parameters are replaced with
`REDACTED`, cookies are dropped, and request/response bodies pass through
`auoreo.core.redaction.redact` before they are written. `tests/unit/test_cassettes.py`
proves the filters on a real recording and scans every committed cassette.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from auoreo.core.redaction import redact

CASSETTES = Path(__file__).resolve().parent / "fixtures" / "cassettes"
REDACTED = "REDACTED"
SECRET_HEADERS = (
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "x-goog-api-key",
    "openai-organization",
    "cookie",
)
SECRET_PARAMS = ("api_key", "apikey", "key", "token", "access_token")
DROP_RESPONSE_HEADERS = frozenset({"set-cookie", "cf-ray", "x-request-id", "request-id"})


def _redact_body(body: Any) -> Any:
    if isinstance(body, bytes):
        try:
            return redact(body.decode("utf-8")).encode("utf-8")
        except UnicodeDecodeError:
            return body
    return redact(body) if isinstance(body, str) else body


def scrub_request(request: Any) -> Any:
    request.body = _redact_body(request.body)
    return request


def scrub_response(response: dict[str, Any]) -> dict[str, Any]:
    headers = response.get("headers") or {}
    response["headers"] = {k: v for k, v in headers.items() if k.lower() not in DROP_RESPONSE_HEADERS}
    body = response.get("body") or {}
    if "string" in body:
        body["string"] = _redact_body(body["string"])
    return response


def config() -> dict[str, Any]:
    """The shared VCR config (also used directly by tests that build their own `vcr.VCR`)."""
    return {
        "filter_headers": [(h, REDACTED) for h in SECRET_HEADERS],
        "filter_query_parameters": [(p, REDACTED) for p in SECRET_PARAMS],
        "before_record_request": scrub_request,
        "before_record_response": scrub_response,
        "decode_compressed_response": True,
        "match_on": ["method", "scheme", "host", "port", "path", "query", "body"],
    }


@pytest.fixture(scope="module")
def vcr_config() -> dict[str, Any]:
    return config()


@pytest.fixture(scope="module")
def vcr_cassette_dir(request: pytest.FixtureRequest) -> str:
    return str(CASSETTES / Path(str(request.node.path)).stem)
