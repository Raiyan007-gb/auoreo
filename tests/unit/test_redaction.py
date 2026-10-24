from __future__ import annotations

import pytest

from auoreo.core.redaction import PREFIXES, mask, redact


@pytest.mark.parametrize(
    "secret",
    [
        "sk-or-v1-0123456789abcdef0123456789",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "AKIAABCDEFGHIJKLMNOP",
        "xoxb-1234567890-abcdefghij",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig1234567890",
    ],
)
def test_prefixed_tokens_are_masked(secret: str) -> None:
    out = redact(f"token is {secret} ok")
    assert secret not in out and "ok" in out


def test_assignment_auth_dsn_and_pem() -> None:
    text = (
        "OPENAI_API_KEY=abcd1234efgh5678\nAuthorization: Bearer abcdefghijklmnop\n"
        "postgres://user:hunter2@db/x\n"
        "-----BEGIN RSA PRIVATE KEY-----\nMII\n-----END RSA PRIVATE KEY-----"
    )
    out = redact(text)
    for leaked in ("abcd1234efgh5678", "abcdefghijklmnop", "hunter2", "MII"):
        assert leaked not in out


def test_mask_shapes_and_prefix_literals() -> None:
    assert mask("short") == "***"
    assert mask("abcdefghijklmnopqrstuvwxyz") == "abcdef…wxyz"
    assert all(len(p) >= 2 for p in PREFIXES)
    assert redact("plain text with sk- nothing") == "plain text with sk- nothing"
