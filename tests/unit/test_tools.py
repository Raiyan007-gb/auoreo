from __future__ import annotations

import json
from pathlib import Path

import pytest

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext

ALLOW_ALL = [Rule(permission="*", pattern="*", action="allow")]


async def call(
    svc: Services,
    workdir: Path,
    name: str,
    args: dict,
    ctx: ToolContext | None = None,
    rules: list[Rule] | None = None,
) -> tuple[str, bool]:
    ctx = ctx or ToolContext(session_id="s1", cwd=workdir, home=svc.settings.home, harness=svc.harness)
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW_ALL if rules is None else rules),
    )
    return out.result.content, out.result.is_error


async def test_read_write_edit_cycle(services: Services, workdir: Path) -> None:
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    text, err = await call(services, workdir, "write", {"path": "a.txt", "content": "one\ntwo\n"}, ctx)
    assert not err and (workdir / "a.txt").read_text() == "one\ntwo\n"
    text, err = await call(services, workdir, "read", {"path": "a.txt"}, ctx)
    assert "     1\tone" in text
    text, err = await call(
        services, workdir, "edit", {"path": "a.txt", "old_string": "two", "new_string": "2"}, ctx
    )
    assert not err and (workdir / "a.txt").read_text() == "one\n2\n"


async def test_edit_requires_read_and_unique_match(services: Services, workdir: Path) -> None:
    (workdir / "b.txt").write_text("x x\n")
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home)
    text, err = await call(
        services, workdir, "edit", {"path": "b.txt", "old_string": "x", "new_string": "y"}, ctx
    )
    assert err and "read" in text
    await call(services, workdir, "read", {"path": "b.txt"}, ctx)
    text, err = await call(
        services, workdir, "edit", {"path": "b.txt", "old_string": "x", "new_string": "y"}, ctx
    )
    assert err and "2 times" in text
    text, err = await call(
        services,
        workdir,
        "edit",
        {"path": "b.txt", "old_string": "x", "new_string": "y", "replace_all": True},
        ctx,
    )
    assert not err and (workdir / "b.txt").read_text() == "y y\n"


async def test_write_refuses_overwrite_without_read(services: Services, workdir: Path) -> None:
    (workdir / "c.txt").write_text("keep")
    text, err = await call(services, workdir, "write", {"path": "c.txt", "content": "new"})
    assert err and (workdir / "c.txt").read_text() == "keep"


async def test_schema_validation_error_is_model_visible(services: Services, workdir: Path) -> None:
    text, err = await call(services, workdir, "read", {"nope": 1})
    assert err and "invalid arguments" in text


async def test_unknown_tool(services: Services, workdir: Path) -> None:
    text, err = await call(services, workdir, "does_not_exist", {})
    assert err and "unknown tool" in text


async def test_bash_runs_with_scrubbed_env(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPER_SECRET_TOKEN", "leak-me-please-123456")
    text, err = await call(services, workdir, "bash", {"command": "echo hi; env"})
    assert "hi" in text and "leak-me" not in text and "[exit code 0]" in text


async def test_bash_timeout(services: Services, workdir: Path) -> None:
    text, err = await call(services, workdir, "bash", {"command": "sleep 5", "timeout_s": 1})
    assert err and "timed out" in text


async def test_secret_file_read_is_denied(services: Services, workdir: Path) -> None:
    (workdir / ".env").write_text("OPENROUTER_API_KEY=sk-or-xyz")
    text, err = await call(services, workdir, "read", {"path": ".env"})
    assert err and "permission denied" in text and "sk-or" not in text


async def test_non_interactive_ask_is_denied(services: Services, workdir: Path) -> None:
    text, err = await call(
        services,
        workdir,
        "bash",
        {"command": "make"},
        rules=[Rule(permission="bash", pattern="*", action="ask")],
    )
    assert err and "non-interactive" in text


async def test_web_fetch_blocks_private_addresses(services: Services, workdir: Path) -> None:
    for url in ("http://127.0.0.1:4096/", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd"):
        text, err = await call(services, workdir, "web_fetch", {"url": url})
        assert err, url


async def test_large_results_spill_to_disk(services: Services, workdir: Path) -> None:
    (workdir / "big.txt").write_text("\n".join(f"line {i} " + "x" * 80 for i in range(3000)))
    text, err = await call(services, workdir, "read", {"path": "big.txt", "limit": 3000})
    assert "full output:" in text and len(text) < 70_000
