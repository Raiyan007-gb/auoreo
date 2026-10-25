"""G1b: redaction modes (mask vs sentinel), new passes, additive pattern registry, and the
write-back guard on edit/write/str_replace/apply_patch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from auoreo.core import redaction
from auoreo.core.permissions import PermissionEngine
from auoreo.core.redaction import (
    contains_redaction_marker,
    redact,
    redact_tool_output,
    register_redaction_patterns,
)
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext

KEY = "sk-ant-" + "api03-Zx9Yw8Vu7Ts6Rq5Po4"
ALLOW = [Rule(permission="*", pattern="*", action="allow")]


def test_sentinel_never_leaks_head_or_tail() -> None:
    out = redact(f"key = {KEY}", "sentinel")
    assert out == "key = «redacted:sk-ant-…»"
    assert KEY[-4:] not in out and KEY[7:13] not in out
    assert redact(KEY) == f"{KEY[:6]}…{KEY[-4:]}"
    assert redact(KEY, "off") == KEY


@pytest.mark.parametrize(
    ("text", "leak"),
    [
        ('{"api_key": "plainvalue123456789"}', "plainvalue123456789"),
        ('{"clientSecret":"abcdef0123456789xyz"}', "abcdef0123456789xyz"),
        ("db:\n  password: hunter2hunter2\n", "hunter2hunter2"),
        ("auth_token: 'zzzyyyxxx111222'", "zzzyyyxxx111222"),
        ("bot 1234567890:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawq", "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsawq"),
        ("fernet gAAAAABlZ0123456789abcdefghij", "gAAAAABlZ0123456789abcdefghij"),
        ("fpk_0123456789abcdefgh", "fpk_0123456789abcdefgh"),
        ("llm-4f8a9b0c1d2e3f4a5b", "llm-4f8a9b0c1d2e3f4a5b"),
        ("r1-a1b2c3d4e5f6g7h8", "r1-a1b2c3d4e5f6g7h8"),
        ("oai-9z8y7x6w5v4u3t2s", "oai-9z8y7x6w5v4u3t2s"),
        ("FAL_KEY=fal0123456789abc", "fal0123456789abc"),
    ],
)
def test_new_passes(text: str, leak: str) -> None:
    for mode in ("mask", "sentinel"):
        assert leak not in redact(text, mode), (mode, redact(text, mode))  # type: ignore[arg-type]


def test_weak_prefixes_spare_ordinary_words() -> None:
    text = "an llm-based pipeline and oai-compatible-endpoint docs"
    assert redact(text) == text


def test_phones_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "call +1 415-555-0134 today"
    assert redact(text) == text
    monkeypatch.setattr(redaction, "PHONES", True)
    assert "555-0134" not in redact(text)


def test_register_patterns_is_additive_and_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redaction, "_EXTRA", {})
    register_redaction_patterns("acme", [r"acme_[0-9a-f]{16}"])
    assert "acme_" not in redact("token acme_0123456789abcdef here") or "…" in redact(
        "token acme_0123456789abcdef here"
    )
    assert "0123456789abcdef" not in redact("acme_0123456789abcdef", "sentinel")
    with pytest.raises(ValueError, match="additive"):
        register_redaction_patterns("acme", [r"x{3}"])
    for bad in (r"(a+)+b", r"", r"x|", r".*secret", r"abc|.*"):
        with pytest.raises(ValueError):
            register_redaction_patterns(f"bad{bad}", [bad])


def test_marker_detection() -> None:
    assert contains_redaction_marker("x = «redacted:sk-ant-…»")
    assert contains_redaction_marker("OPENAI_API_KEY=sk-pro…z123")
    assert contains_redaction_marker('password: "***"')
    assert contains_redaction_marker('{"api_key": "abcdef…wxyz"}')
    assert contains_redaction_marker("Loading… done") is None
    assert contains_redaction_marker("wait…") is None


def test_tool_output_mode_selection() -> None:
    st = Settings()
    assert "«redacted:" in redact_tool_output(KEY, mode="sentinel", permission="read", settings=st)
    assert "…" in redact_tool_output(KEY, mode="mask", permission="webfetch", settings=st)
    off = Settings(security={"redact_terminal_output": False})
    assert redact_tool_output(KEY, mode="mask", permission="bash", settings=off) == KEY
    assert redact_tool_output(KEY, mode="mask", permission="bash", settings=st) != KEY


def test_read_like_tools_declare_sentinel(services: Services) -> None:
    for name in ("read", "doc_read", "sheet_read", "grep"):
        assert services.harness.tools[name].manifest.redaction == "sentinel", name
    assert services.harness.tools["bash"].manifest.redaction == "mask"


async def _call(svc: Services, cwd: Path, name: str, args: dict, ctx: ToolContext) -> tuple[str, bool]:
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW),
    )
    return out.result.content, out.result.is_error


async def test_read_sentinel_and_write_back_refused(services: Services, workdir: Path) -> None:
    cfg = workdir / "config.py"
    cfg.write_text(f'API = "{KEY}"\nDEBUG = True\n')
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home, harness=services.harness)
    text, err = await _call(services, workdir, "read", {"path": "config.py"}, ctx)
    print(text)
    assert not err and "«redacted:sk-ant-…»" in text and KEY not in text
    redacted_line = 'API = "«redacted:sk-ant-…»"\nDEBUG = False\n'
    text, err = await _call(services, workdir, "write", {"path": "config.py", "content": redacted_line}, ctx)
    print(text)
    assert err and "refusing to write redacted text" in text
    for tool in ("edit", "str_replace"):
        text, err = await _call(
            services,
            workdir,
            tool,
            {
                "path": "config.py",
                "old_string": "DEBUG = True",
                "new_string": 'DEBUG = False\nK = "sk-pro…z123"',
            },
            ctx,
        )
        assert err and "redacted" in text, tool
    patch = "*** Begin Patch\n*** Add File: new.env.py\n+TOKEN = '«redacted:ghp_…»'\n*** End Patch"
    text, err = await _call(services, workdir, "apply_patch", {"patch": patch}, ctx)
    assert err and "redacted" in text and not (workdir / "new.env.py").exists()
    assert KEY in cfg.read_text()  # the real secret survived every attempt
    text, err = await _call(
        services,
        workdir,
        "edit",
        {"path": "config.py", "old_string": "DEBUG = True", "new_string": "DEBUG = 1"},
        ctx,
    )
    assert not err and KEY in cfg.read_text()  # editing around the secret still works
