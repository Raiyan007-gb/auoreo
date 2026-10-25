from __future__ import annotations

from pathlib import Path

from auoreo.core import extensions
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.harness.discovery import Root
from auoreo.harness.registry import Harness
from tests.conftest import REPO, write


def test_hook_job_webhook_plugin_kinds_load(tmp_path: Path) -> None:
    write(
        tmp_path / "hooks" / "lint.yaml",
        "name: lint\ndescription: d\nevent: PostToolUse\nmatcher: 'edit|write'\ncommand: [./lint.sh]\n",
    )
    write(
        tmp_path / "jobs" / "brief.yaml",
        "name: brief\ndescription: d\nschedule: '0 9 * * 1-5'\nprompt: 'It is {{now}}'\n",
    )
    write(
        tmp_path / "webhooks" / "gh.yaml",
        "name: gh\ndescription: d\nprovider: github\nsecret: 'secret:webhook/gh'\n"
        "events: [pull_request]\nfilters: [{field: action, equals: opened}]\n"
        "prompt: 'PR {{event.pull_request.title}} on {{route}}'\n",
    )
    d = tmp_path / "plugins" / "demo"
    write(d / "plugin.yaml", "name: demo\nversion: 0.1.0\ndescription: d\nprovides: [tools]\n")
    write(d / "pyproject.toml", "[project]\nname='auoreo-plugin-demo'\nversion='0.1.0'\n")
    write(d / "plugin.py", "class Plugin: ...\n")
    h = Harness([Root("runtime", tmp_path), Root("bundled", REPO)])
    assert h.errors == []
    assert h.hooks["lint"].manifest.event == "PostToolUse"
    assert h.jobs["brief"].manifest.deliver == ["log"]
    assert h.webhooks["gh"].manifest.filters[0].field == "action"
    assert h.plugins["demo"].manifest.entry == "plugin.py:Plugin"
    listing = h.describe()
    assert {r["name"] for r in listing["hooks"]} == {"lint"} and listing["plugins"][0]["name"] == "demo"


def test_m5_manifest_errors(tmp_path: Path) -> None:
    write(tmp_path / "webhooks" / "raw.yaml", "name: raw\ndescription: d\nsecret: 'hunter2'\nprompt: 'x'\n")
    write(
        tmp_path / "webhooks" / "badre.yaml",
        "name: badre\ndescription: d\nsecret: 'secret:x'\nprompt: 'x'\nfilters: [{field: a, regex: '('}]\n",
    )
    write(
        tmp_path / "jobs" / "badvar.yaml",
        "name: badvar\ndescription: d\nschedule: 'every 1h'\nprompt: '{{nope}}'\n",
    )
    write(tmp_path / "hooks" / "nocmd.yaml", "name: nocmd\ndescription: d\nevent: Stop\ncommand: []\n")
    write(
        tmp_path / "plugins" / "nopy" / "plugin.yaml",
        "name: nopy\nversion: 1.0.0\ndescription: d\nprovides: [tools]\n",
    )
    write(tmp_path / "plugins" / "nopy" / "plugin.py", "class Plugin: ...\n")
    h = Harness([Root("runtime", tmp_path), Root("bundled", REPO)])
    msgs = " | ".join(str(e) for e in h.errors)
    assert "secret:<name>" in msgs and "bad filter regex" in msgs
    assert "unknown template variable" in msgs and "pyproject.toml" in msgs
    assert "command" in msgs
    assert not any(k in h.webhooks for k in ("raw", "badre")) and "nopy" not in h.plugins


async def test_extension_seam_starts_and_closes(home: Path, workdir: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls: list[str] = []

    class Ext:
        async def close(self) -> None:
            calls.append("close")

    async def start(svc: Services) -> Ext:
        calls.append("start")
        return Ext()

    async def broken(svc: Services) -> None:
        raise RuntimeError("boom")

    import types

    mod = types.ModuleType("auoreo_test_ext")
    mod.start = start  # type: ignore[attr-defined]
    mod.broken = broken  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "auoreo_test_ext", mod)
    monkeypatch.setattr(extensions, "STARTERS", ["auoreo_test_ext:start", "auoreo_test_ext:broken"])
    svc = await Services.create(Settings(home=home, gateway="fake"), cwd=workdir, with_mcp=False)
    assert isinstance(svc.ext["auoreo_test_ext"], Ext) and calls == ["start"]
    await svc.close()
    assert calls == ["start", "close"]
