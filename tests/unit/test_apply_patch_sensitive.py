"""apply_patch and the sensitive-write floor (spec §19.7).

The executor sees `apply_patch` as permission `edit` with pattern `*` (its paths live inside
the patch text), so `SENSITIVE_WRITE_GLOBS` (`$AUOREO_HOME/hosts/*`, `$AUOREO_HOME/ssh/*`,
`settings.json`, ...) never matched: one `edit *` allow rule, `acceptEdits` or `yolo` wrote them
without the always-ask the `edit` tool gets. The handler asks per sensitive path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.core.services import Services
from tests.unit.test_tools_m2 import call, ctx_for


def add_file(path: Path) -> str:
    return f"*** Begin Patch\n*** Add File: {path}\n+x: 1\n*** End Patch"


@pytest.mark.parametrize("rel", ["hosts/evil.yaml", "ssh/known_hosts", "settings.json"])
async def test_sensitive_target_asks_even_under_an_allow_rule(
    services: Services, workdir: Path, rel: str
) -> None:
    ctx = ctx_for(services, workdir)  # engine: `* * allow`; non-interactive, so an ask is a refusal
    target = services.settings.home / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    text, err = await call(services, ctx, "apply_patch", {"patch": add_file(target)})
    assert err and "needs approval" in text, text
    assert not target.exists()


async def test_moving_onto_a_sensitive_target_asks(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    src = workdir / "plain.yaml"
    src.write_text("x: 1\n")
    target = services.settings.home / "hosts" / "evil.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    patch = f"*** Begin Patch\n*** Update File: {src}\n*** Move to: {target}\n@@\n-x: 1\n+x: 2\n*** End Patch"
    text, err = await call(services, ctx, "apply_patch", {"patch": patch})
    assert err and "needs approval" in text, text
    assert not target.exists() and src.read_text() == "x: 1\n"


async def test_ordinary_paths_and_dry_runs_are_not_asked(services: Services, workdir: Path) -> None:
    ctx = ctx_for(services, workdir)
    plain = workdir / "notes.md"
    text, err = await call(services, ctx, "apply_patch", {"patch": add_file(plain)})
    assert not err and plain.exists(), text
    target = services.settings.home / "hosts" / "evil.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    text, err = await call(services, ctx, "apply_patch", {"patch": add_file(target), "dry_run": True})
    assert not err and not target.exists(), text
