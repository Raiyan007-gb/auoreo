"""G1b: Landlock layer (bwrap + opt-in none) and the macOS seatbelt profile generator."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from auoreo.backends import landlock
from auoreo.backends.sandbox import ExecSpec
from auoreo.core.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
ABI = landlock.abi()
ESCAPE = Path.home() / ".g1b-landlock-escape-probe"
needs_landlock = pytest.mark.skipif(ABI <= 0, reason="kernel has no Landlock")


def _impl(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"g1b_{name}", ROOT / "backends" / "sandbox" / name / "impl.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_probe_is_honest() -> None:
    text = landlock.describe()
    print(f"landlock probe: abi={ABI} -> {text}")
    assert ("ABI" in text) == (ABI > 0)
    assert landlock.describe(0).startswith("landlock: unavailable")
    assert "net" in landlock.describe(4) and "net" not in landlock.describe(3)


def test_wrap_and_roots_are_pure(tmp_path: Path) -> None:
    argv = landlock.wrap(
        ["echo", "hi"], cwd=tmp_path, mode="read-only", python="/usr/bin/python3", tmp="/tmp"
    )
    assert argv[:4] == ["/usr/bin/python3", "-I", "-S", "-c"] and argv[-2:] == ["echo", "hi"]
    cfg = json.loads(argv[5])
    assert str(tmp_path) in cfg["ro"] and str(tmp_path) not in cfg["rw"] and "/tmp" in cfg["rw"]
    assert "/" not in cfg["ro"] and cfg["require"] is True
    ro, rw = landlock.roots(tmp_path, "workspace-write", writable=[tmp_path / "out"], tmp="/tmp")
    assert str(tmp_path) in rw and str(tmp_path / "out") in rw and "/usr" in ro


def test_bwrap_argv_layers_landlock_inside(tmp_path: Path) -> None:
    bw = _impl("bwrap")
    spec = ExecSpec(argv=["true"], cwd=tmp_path)
    plain = bw.build_argv(spec, "bwrap")
    layered = bw.build_argv(spec, "bwrap", "/usr/bin/python3")
    sep = layered.index("--")
    assert layered[:sep] == plain[: plain.index("--")]  # bwrap options unchanged
    assert layered[sep + 1 : sep + 5] == ["/usr/bin/python3", "-I", "-S", "-c"] and layered[-1] == "true"


@needs_landlock
def test_landlock_helper_confines_writes(tmp_path: Path) -> None:
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside.txt"
    script = f"echo in > inside.txt; echo out > {outside}; echo rc=$?"
    argv = landlock.wrap(["sh", "-c", script], cwd=work, mode="workspace-write", tmp=str(work / "t"))
    r = subprocess.run(argv, cwd=work, capture_output=True, text=True, timeout=30)
    print(r.stdout, r.stderr)
    assert (work / "inside.txt").read_text() == "in\n"
    assert not outside.exists() and "rc=2" in r.stdout
    ro = landlock.wrap(["sh", "-c", "echo x > ro.txt"], cwd=work, mode="read-only", tmp=str(work / "t"))
    r = subprocess.run(ro, cwd=work, capture_output=True, text=True, timeout=30)
    assert r.returncode != 0 and not (work / "ro.txt").exists()


@needs_landlock
async def test_none_backend_with_sandbox_landlock(tmp_path: Path) -> None:
    none = _impl("none")
    st = Settings(security={"sandbox_landlock": True})
    backend = none.Backend(st)
    status = await backend.check()
    assert status.ok and f"ABI {ABI}" in status.detail and backend.capabilities()["fs_isolation"]
    res = await backend.run(
        ExecSpec(argv=["sh", "-c", f"echo x > {ESCAPE}"], cwd=tmp_path, env={"PATH": "/usr/bin:/bin"})
    )
    assert res.enforced and res.exit_code != 0 and not ESCAPE.exists()
    assert not (await none.Backend(Settings()).check()).detail.count("landlock")


@needs_landlock
@pytest.mark.skipif(
    shutil.which("bwrap") is None or landlock.system_python() is None, reason="needs bwrap + /usr/bin/python3"
)
async def test_bwrap_backend_reports_and_enforces(tmp_path: Path) -> None:
    bw = _impl("bwrap")
    backend = bw.Backend(Settings())
    status = await backend.check()
    print(status.detail)
    if not status.ok:
        pytest.skip(status.detail)
    assert f"ABI {ABI}" in status.detail and "layered" in status.detail
    res = await backend.run(
        ExecSpec(
            argv=["sh", "-c", "echo ok > a.txt && cat a.txt"], cwd=tmp_path, env={"PATH": "/usr/bin:/bin"}
        )
    )
    if "Operation not permitted" in res.stderr or "No permissions" in res.stderr:
        pytest.skip(f"bwrap cannot create namespaces here: {res.stderr.strip()}")
    assert res.exit_code == 0 and res.stdout.strip() == "ok" and res.enforced


# ------------------------------------------------------------------- seatbelt
def test_seatbelt_profile_generation(tmp_path: Path) -> None:
    sb = _impl("seatbelt")
    ws = sb.build_profile(ExecSpec(argv=["ls"], cwd=tmp_path, net="none"), tmpdir="/private/tmp/x")
    assert ws.startswith("(version 1)\n(deny default)")
    assert '(allow file-read* file-write* (subpath "/private/tmp")' in ws
    assert f'(subpath "{tmp_path}")' in ws and "network" not in ws
    assert '(allow file-write* (subpath "/"))' not in ws  # never full-disk write
    ro = sb.build_profile(ExecSpec(argv=["ls"], cwd=tmp_path, mode="read-only"), tmpdir="/private/tmp")
    assert f'(allow file-read* (subpath "{tmp_path}"))' in ro
    assert f'file-write* (subpath "{tmp_path}")' not in ro
    proxy = sb.build_profile(ExecSpec(argv=["ls"], cwd=tmp_path, net="proxy"))
    assert '(allow network-outbound (remote ip "localhost:*"))' in proxy
    assert "(allow network-outbound)" in sb.build_profile(ExecSpec(argv=["ls"], cwd=tmp_path, net="direct"))
    with pytest.raises(ValueError):
        sb.build_profile(ExecSpec(argv=["ls"], cwd=tmp_path, mode="danger-full-access"))
    quoted = sb.build_profile(ExecSpec(argv=["ls"], cwd=Path('/w/we"ird')))
    assert '(subpath "/w/we\\"ird")' in quoted
    argv = sb.build_argv(ExecSpec(argv=["ls", "-la"], cwd=tmp_path))
    assert argv[0] == "/usr/bin/sandbox-exec" and argv[1] == "-p" and argv[-2:] == ["ls", "-la"]


async def test_seatbelt_check_is_honest_off_macos(tmp_path: Path) -> None:
    sb = _impl("seatbelt")
    status = await sb.Backend(Settings()).check()
    if sys.platform != "darwin":
        assert not status.ok and "macOS-only" in status.detail
        res = await sb.Backend(Settings()).run(ExecSpec(argv=["true"], cwd=tmp_path))
        assert res.exit_code == 127 and not res.enforced
