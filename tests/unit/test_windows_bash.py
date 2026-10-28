"""On Windows a bare `bash` is Git Bash, never the WSL launcher stubs (System32 / WindowsApps)."""

from __future__ import annotations

from pathlib import Path

import pytest

from auoreo.backends import sandbox


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    sandbox.windows_bash.cache_clear()


def _exe(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "bash.exe"
    exe.write_bytes(b"")
    return exe


def test_skips_wsl_stubs_and_finds_git_bash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _exe(tmp_path / "Windows" / "System32")
    apps = _exe(tmp_path / "Microsoft" / "WindowsApps")
    git = _exe(tmp_path / "Git" / "usr" / "bin")
    monkeypatch.setenv(
        "PATH", f"{stub.parent}{sandbox.os.pathsep}{apps.parent}{sandbox.os.pathsep}{git.parent}"
    )
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    for var in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    assert sandbox.windows_bash() == str(git)


def test_none_when_only_stubs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _exe(tmp_path / "Windows" / "System32")
    monkeypatch.setenv("PATH", str(stub.parent))
    monkeypatch.setattr(sandbox.shutil, "which", lambda _name: None)
    for var in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    assert sandbox.windows_bash() is None


def test_host_argv_rewrites_bash_on_windows_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sandbox, "windows_bash", lambda: "C:/Git/bin/bash.exe")
    monkeypatch.setattr(sandbox.sys, "platform", "win32")
    assert sandbox.host_argv(["bash", "-c", "echo hi"]) == ["C:/Git/bin/bash.exe", "-c", "echo hi"]
    assert sandbox.host_argv(["python", "-V"]) == ["python", "-V"]
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    assert sandbox.host_argv(["bash", "-c", "echo hi"]) == ["bash", "-c", "echo hi"]
