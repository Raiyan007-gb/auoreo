"""`Settings.locale` reaches the Tier-2 context only (the stable prompt stays byte-stable)."""

from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.core.settings import Settings, get_settings
from auoreo.gateway.fake import FakeGateway
from auoreo.harness.orchestrator import build_spec, render_context


def test_locale_in_context_not_system_prompt(services: Services, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = build_spec(services.harness, "default", "default", "manual")
    assert "{{locale}}" not in spec.system_prompt and "Reply" not in spec.context_template
    assert "locale" in spec.system_prompt  # deliverable.md: generic instruction only
    assert "locale: de-DE" in render_context(spec, Path("."), "manual", locale="de-DE")
    get_settings.cache_clear()
    monkeypatch.setenv("AUOREO_LOCALE", "fr-FR")
    try:
        assert "locale: fr-FR" in render_context(spec, Path("."), "manual")
    finally:
        get_settings.cache_clear()
    monkeypatch.delenv("AUOREO_LOCALE")
    assert "locale: \n" in render_context(spec, Path("."), "manual") + "\n"


async def test_locale_reaches_the_model(home: Path, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUOREO_LOCALE", "ja-JP")
    get_settings.cache_clear()
    gateway = FakeGateway()
    svc = await Services.create(
        Settings(home=home, gateway="fake", locale="ja-JP"), cwd=workdir, gateway=gateway, with_mcp=False
    )
    try:
        sess = await svc.create_session()
        await svc.coordinator.submit(sess.id, "hello", interactive=False)
        await svc.coordinator.wait(sess.id, 10)
        main = [r for r in gateway.requests if r.metadata.get("purpose") not in {"title", "compaction"}]
        assert any("locale: ja-JP" in str(m.get("content")) for m in main[0].messages)
    finally:
        await svc.close()
        get_settings.cache_clear()
