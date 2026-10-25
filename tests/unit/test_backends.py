from __future__ import annotations

import sys
from pathlib import Path

import pytest

from auoreo.backends import BackendStatus
from auoreo.core.services import Services
from auoreo.harness.discovery import Root
from auoreo.harness.registry import Harness
from auoreo.tools.context import ToolContext, ToolError
from tests.conftest import REPO, write

IMPL = """
from auoreo.backends import BackendStatus

class Backend:
    def __init__(self, settings):
        self.settings = settings
        self.closed = False
    async def check(self):
        return BackendStatus(True, "ready")
    def ping(self):
        return "%s"
    async def close(self):
        self.closed = True
"""


def make_backend(root: Path, kind: str, name: str, extra: str = "") -> None:
    d = root / "backends" / kind / name
    write(d / "backend.yaml", f"kind: {kind}\nname: {name}\ndescription: test {name}\n{extra}")
    write(d / "impl.py", IMPL % name)


def test_two_level_discovery_and_lazy_import(tmp_path: Path) -> None:
    make_backend(tmp_path, "widget", "alpha")
    make_backend(tmp_path, "widget", "beta", "default: true\n")
    h = Harness([Root("bundled", tmp_path)])
    assert set(h.snapshot.backends) == {"widget/alpha", "widget/beta"}
    assert "auoreo_backend_widget_alpha" not in sys.modules
    assert h.snapshot.backends["widget/alpha"].load().__name__ == "Backend"
    assert "auoreo_backend_widget_alpha" in sys.modules


def test_selection_rules(tmp_path: Path) -> None:
    make_backend(tmp_path, "widget", "alpha")
    make_backend(tmp_path, "widget", "beta", "default: true\n")
    make_backend(tmp_path, "single", "only")
    h = Harness([Root("bundled", tmp_path)])
    assert h.select_backend("widget", None).manifest.name == "beta"  # default wins
    assert h.select_backend("widget", "alpha").manifest.name == "alpha"  # settings win
    assert h.select_backend("single", None).manifest.name == "only"  # sole candidate
    assert h.select_backend("missing", None) is None
    with pytest.raises(KeyError):
        h.select_backend("widget", "gamma")


def test_two_defaults_is_an_error(tmp_path: Path) -> None:
    make_backend(tmp_path, "widget", "a", "default: true\n")
    make_backend(tmp_path, "widget", "b", "default: true\n")
    h = Harness([Root("bundled", tmp_path)])
    with pytest.raises(KeyError):
        h.select_backend("widget", None)


def test_manifest_errors(tmp_path: Path) -> None:
    d = tmp_path / "backends" / "widget" / "wrong"
    write(d / "backend.yaml", "kind: widget\nname: other\ndescription: x\n")
    write(d / "impl.py", IMPL % "x")
    write(
        tmp_path / "backends" / "widget" / "noimpl" / "backend.yaml",
        "kind: widget\nname: noimpl\ndescription: x\n",
    )
    h = Harness([Root("runtime", tmp_path), Root("bundled", REPO)])
    msgs = " ".join(str(e) for e in h.errors)
    assert "must match the folders" in msgs and "impl file" in msgs


async def test_services_backend_cache_and_ctx_accessor(services: Services, home: Path) -> None:
    make_backend(home, "widget", "alpha")
    services.harness.reload()
    inst = services.backend("widget")
    assert inst is services.backend("widget") and inst.ping() == "alpha"
    assert isinstance(await inst.check(), BackendStatus)
    ctx = ToolContext(session_id="s", cwd=home, home=home, services={"services": services})
    assert ctx.backend("widget") is inst
    with pytest.raises(ToolError):
        ctx.backend("nope")
    await services.close_backends()
    assert inst.closed


def test_doctor_reports_backends(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from auoreo.cli.commands.doctor import _backend_checks
    from auoreo.core.settings import Settings

    make_backend(home, "widget", "alpha", "requires_binaries: [definitely-not-a-binary]\n")
    make_backend(home, "gadget", "fake", "posture: nc-opt-in\n")
    h = Harness([Root("runtime", home), Root("bundled", REPO)])
    rows = {r["check"]: r for r in _backend_checks(Settings(home=home), h)}
    assert rows["backend:widget"]["level"] == "warn" and "missing" in rows["backend:widget"]["detail"]
    assert rows["backend:gadget"]["level"] == "ok" and "[nc-opt-in]" in rows["backend:gadget"]["detail"]
