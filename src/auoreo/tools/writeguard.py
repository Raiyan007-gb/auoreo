"""Write-back guard (spec §19.10, §25.10): file writers refuse content that carries a
redaction sentinel (`«redacted:…»`) or a display mask (`sk-pro…abcd`, `KEY=***`).

A model that read a redacted file and writes it back would otherwise replace real secrets
with their redacted form (the classic write-back 401). Handlers call `check_write()` on the
text they are about to write; the refusal is a `ToolError` so the model sees why.
"""

from __future__ import annotations

from auoreo.core.redaction import contains_redaction_marker
from auoreo.tools.context import ToolError


def write_back_reason(text: str) -> str | None:
    marker = contains_redaction_marker(text)
    if marker is None:
        return None
    return (
        f"refusing to write redacted text ({marker[:60]!r}) into a file: it would replace the real "
        "secret with its redacted form. Leave secret values untouched (edit around them) or ask "
        "the user to set them"
    )


def check_write(text: str, *, original: str | None = None) -> None:
    """Raise `ToolError` if `text` would write a redaction marker that is not already in
    `original` (an existing marker in the file — e.g. docs about redaction — is allowed)."""
    reason = write_back_reason(text)
    if reason is None:
        return
    if original is not None and contains_redaction_marker(original) == contains_redaction_marker(text):
        return
    raise ToolError(reason)
