"""I1 §19.10: secrets split by zero-width / control characters are still redacted, and the
text outside the secret keeps its invisible characters (spans mapped back)."""

from __future__ import annotations

import pytest

from auoreo.core import redaction
from auoreo.core.redaction import redact

KEY = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0" * 2
BODY = KEY[8:]


@pytest.fixture(autouse=True)
def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redaction, "ENABLED", True)


def _split(s: str, sep: str, every: int = 5) -> str:
    return sep.join(s[i : i + every] for i in range(0, len(s), every))


@pytest.mark.parametrize("sep", ["\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\x00", "\x1b", "\x7f"])
def test_split_secret_is_redacted(sep: str) -> None:
    text = f"key: {_split(KEY, sep)} end"
    out = redact(text)
    assert BODY[4:-4] not in out.replace(sep, "")
    assert out.startswith("key: ") and out.endswith(" end")


def test_invisible_chars_outside_the_secret_are_preserved() -> None:
    text = f"he\u200bllo {_split(KEY, chr(0x200B))} wo\u2060rld"
    out = redact(text)
    assert out.startswith("he\u200bllo ") and out.endswith(" wo\u2060rld")
    assert BODY[4:-4] not in out.replace("\u200b", "")


def test_env_assignment_split_by_zero_width() -> None:
    secret = "hunter2-very-secret-value"
    out = redact("DB_PASSWORD=hun\u200bter2-very-sec\u200dret-value")
    assert secret not in out.replace("\u200b", "").replace("\u200d", "")
    assert out.startswith("DB_PASSWORD=")


def test_plain_text_with_invisible_chars_is_unchanged() -> None:
    text = "nothing\u200b secret\x07 here\ufeff"
    assert redact(text) == text


def test_tabs_and_newlines_are_not_stripped() -> None:
    text = f"a\tb\n{KEY}\r\n"
    out = redact(text)
    assert out.startswith("a\tb\n") and out.endswith("\r\n") and BODY not in out
