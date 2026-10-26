"""H5: grep's pure-Python fallback, rg/ffmpeg doctor rows, Dockerfile ripgrep, LSP rust/go
entries and the apply_patch format step."""

from __future__ import annotations

import json
import shutil
import stat
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from auoreo.core.diagnostics_tools import tool_binary_checks
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.core.settings_h5 import H5Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools import lsp_client
from auoreo.tools.context import ToolContext, ToolResult
from auoreo.tools.formatters import format_files
from auoreo.tools.pysearch import SearchOptions, parse_ignore, search

REPO = Path(__file__).resolve().parents[2]
ALLOW = [Rule(permission="*", pattern="*", action="allow")]


@pytest.fixture
async def services(settings: Settings, workdir: Path, gateway: FakeGateway) -> AsyncIterator[Services]:
    svc = await Services.create(settings, cwd=workdir, gateway=gateway, with_mcp=False)
    try:
        yield svc
    finally:
        await svc.close()


async def call(svc: Services, workdir: Path, name: str, args: dict) -> ToolResult:
    ctx = ToolContext(
        session_id="s1",
        cwd=workdir,
        home=svc.settings.home,
        harness=svc.harness,
        services={"services": svc, "engine": PermissionEngine(list(ALLOW))},
    )
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=PermissionEngine(ALLOW),
    )
    return out.result


def tree(root: Path) -> Path:
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("needle in git dir\n")
    (root / ".gitignore").write_text("build/\n*.log\n!keep.log\n/rooted.txt\n")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\nneedle = 2\ny = 3\n")
    (root / "src" / "b.txt").write_text("Needle upper\n")
    (root / "src" / ".hidden.py").write_text("needle hidden\n")
    (root / "src" / ".gitignore").write_text("local_only.py\n")
    (root / "src" / "local_only.py").write_text("needle local\n")
    (root / "build").mkdir()
    (root / "build" / "out.py").write_text("needle built\n")
    (root / "app.log").write_text("needle log\n")
    (root / "keep.log").write_text("needle kept\n")
    (root / "rooted.txt").write_text("needle rooted\n")
    (root / "src" / "rooted.txt").write_text("needle nested rooted\n")
    (root / "blob.bin").write_bytes(b"needle\x00\x01\x02")
    return root


# ------------------------------------------------------------------ pysearch
def test_pysearch_respects_gitignore_binary_hidden(tmp_path: Path) -> None:
    root = tree(tmp_path / "repo")
    found = {
        Path(p).relative_to(root).as_posix() for p in search(root, SearchOptions("needle", mode="files"))
    }
    assert found == {"src/a.py", "src/.hidden.py", "keep.log", "src/rooted.txt"}
    insensitive = search(root, SearchOptions("needle", mode="files", case_insensitive=True))
    assert any(p.endswith("b.txt") for p in insensitive)


def test_pysearch_modes_glob_context_and_caps(tmp_path: Path) -> None:
    root = tree(tmp_path / "repo")
    content = search(root, SearchOptions("needle", glob="*.py"))
    assert f"{root}/src/a.py:2:needle = 2" in content and all(".py:" in ln for ln in content)
    ctx = search(root / "src" / "a.py", SearchOptions("needle", context=1))
    assert ctx == ["1-x = 1", "2:needle = 2", "3-y = 3"]
    counts = search(root, SearchOptions("needle", mode="count", glob="a.py"))
    assert counts == [f"{root}/src/a.py:1"]
    assert search(root, SearchOptions("needle", mode="files", max_files=1)) in ([f"{root}/keep.log"], [])
    big = root / "src" / "big.py"
    big.write_text("needle\n" * 10)
    assert not search(big, SearchOptions("needle", max_file_bytes=10))


def test_parse_ignore_negation_and_dir_only() -> None:
    rules = parse_ignore("# c\n\nnode_modules/\n!important.txt\n*.tmp\n")
    assert [r.dir_only for r in rules] == [True, False, False] and rules[1].negate


async def test_grep_tool_falls_back_without_rg(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree(workdir)
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name == "rg" else real(name, *a, **k))
    res = await call(services, workdir, "grep", {"pattern": "needle", "output_mode": "files"})
    assert not res.is_error, res.content
    assert "src/a.py" in res.content and "build/out.py" not in res.content and "blob.bin" not in res.content
    assert "Python fallback" in res.content
    res = await call(services, workdir, "grep", {"pattern": "("})
    assert res.is_error and "invalid regex" in res.content
    res = await call(services, workdir, "grep", {"pattern": "zzz-not-there"})
    assert res.content.startswith("no matches")
    services.settings.h5.grep.python_fallback = False
    res = await call(services, workdir, "grep", {"pattern": "needle"})
    assert res.is_error and "not installed" in res.content


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
async def test_grep_rg_and_fallback_agree(
    services: Services, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree(workdir)
    with_rg = await call(services, workdir, "grep", {"pattern": "needle", "output_mode": "files"})
    real = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None if name == "rg" else real(name, *a, **k))
    without = await call(services, workdir, "grep", {"pattern": "needle", "output_mode": "files"})
    assert set(with_rg.content.splitlines()) == set(without.content.splitlines()[:-1])


# ------------------------------------------------------------------ doctor + docker
def test_doctor_rows_for_rg_and_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: "/usr/bin/rg" if name == "rg" else None)
    rows = {r.check: r for r in tool_binary_checks()}
    assert rows["rg"].level == "ok" and rows["rg"].detail == "/usr/bin/rg"
    assert rows["ffmpeg"].level == "warn" and "install ffmpeg" in rows["ffmpeg"].remedy


async def test_doctor_report_includes_rows(home: Path) -> None:
    from auoreo.core import diagnostics

    names = [c.check for c in await diagnostics.run_checks(Settings(home=home, gateway="fake"))]
    assert names.index("rg") < names.index("credentials") and "ffmpeg" in names


def test_dockerfile_runtime_installs_ripgrep() -> None:
    text = (REPO / "Dockerfile").read_text()
    runtime = text[text.index("AS runtime") :]
    assert "apt-get install -y --no-install-recommends ripgrep" in runtime


# ------------------------------------------------------------------ LSP
def test_lsp_rust_and_go_entries(tmp_path: Path) -> None:
    assert lsp_client.EXT_LANG[".rs"] == ("rust", "rust") and lsp_client.EXT_LANG[".go"] == ("go", "go")
    assert ["rust-analyzer"] in lsp_client.LSP_SERVERS["rust"] and ["gopls"] in lsp_client.LSP_SERVERS["go"]
    assert "rustup component add rust-analyzer" in lsp_client.REMEDY["rust"]
    assert "gopls@latest" in lsp_client.REMEDY["go"]
    crate = tmp_path / "crate"
    (crate / "src").mkdir(parents=True)
    (crate / "Cargo.toml").write_text("[package]\nname='x'\n")
    assert lsp_client.find_root(crate / "src" / "main.rs", "rust", tmp_path) == crate


# ------------------------------------------------------------------ format step
def _patch(path: str, body: str) -> str:
    return f"*** Begin Patch\n*** Add File: {path}\n{body}*** End Patch\n"


UGLY = "+x  =  {'a':1}\n+def f( a ):\n+  return a\n"


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not on PATH")
async def test_apply_patch_formats_with_project_ruff(services: Services, workdir: Path) -> None:
    (workdir / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n")
    res = await call(services, workdir, "apply_patch", {"patch": _patch("m.py", UGLY)})
    assert not res.is_error, res.content
    assert "formatted: m.py" in res.content and res.meta["formatted"] == ["m.py"]
    assert (workdir / "m.py").read_text() == 'x = {"a": 1}\n\n\ndef f(a):\n    return a\n'


async def test_apply_patch_no_project_formatter_or_opt_out(services: Services, workdir: Path) -> None:
    res = await call(services, workdir, "apply_patch", {"patch": _patch("m.py", UGLY)})
    assert not res.is_error and "formatted" not in res.content and res.meta["formatted"] == []
    (workdir / "ruff.toml").write_text("line-length = 100\n")
    services.settings.h5.apply_patch.format = False
    res = await call(services, workdir, "apply_patch", {"patch": _patch("n.py", UGLY)})
    assert not res.is_error and "formatted" not in res.content
    assert (workdir / "n.py").read_text().startswith("x  =  {")


def _fake_exe(path: Path, script: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


async def test_format_files_prettier_and_fail_open(tmp_path: Path) -> None:
    proj = tmp_path / "web"
    # a fake prettier that upper-cases its file arguments' content (skips its own flags)
    _fake_exe(
        proj / "node_modules" / ".bin" / "prettier",
        'for f in "$@"; do case "$f" in -*|warn) ;; '
        '*) tr a-z A-Z < "$f" > "$f.t" && mv "$f.t" "$f";; esac; done',
    )
    js = proj / "src" / "a.ts"
    js.parent.mkdir()
    js.write_text("let a = 1\n")
    same = proj / "src" / "b.ts"
    same.write_text("CONST B = 2\n")
    report = await format_files([js, same, proj / "gone.ts"], {"PATH": "/usr/bin:/bin"})
    assert report.formatted == [js] and js.read_text() == "LET A = 1\n" and not report.notes
    _fake_exe(proj / "node_modules" / ".bin" / "prettier", "echo boom >&2; exit 2")
    report = await format_files([js], {"PATH": "/usr/bin:/bin"})
    assert report.formatted == [] and report.notes and "exited 2" in report.notes[0]


@pytest.mark.skipif(shutil.which("rustfmt") is None, reason="rustfmt not installed")
async def test_format_files_rustfmt_needs_cargo(tmp_path: Path) -> None:
    rs = tmp_path / "src" / "main.rs"
    rs.parent.mkdir()
    rs.write_text('fn main(){println!("hi");}\n')
    assert (await format_files([rs], {})).formatted == []  # no Cargo.toml: not a rust project
    (tmp_path / "Cargo.toml").write_text("[package]\nname = 'x'\nversion = '0.1.0'\n")
    report = await format_files([rs], {})
    assert (
        report.formatted == [rs] and 'println!("hi");' in rs.read_text() and "fn main() {" in rs.read_text()
    )


def test_h5_settings_defaults() -> None:
    h5 = H5Settings()
    assert h5.apply_patch.format and h5.grep.python_fallback and h5.computer_use.lock
    assert Settings().h5.forecast.max_samples == 2000
