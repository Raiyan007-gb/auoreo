"""Install a plugin into `<home>/plugins/<name>/.venv` and pin it in `lock.json`.

`uv venv --python <ver>` creates the venv (stdlib `venv` when `uv` is not on PATH). If the
plugin's `pyproject.toml` declares a `[build-system]`, the folder itself is installed with
`uv pip install --python <venv> <folder>`; otherwise only its `[project].dependencies` are
(a `requirements.lock` with hashes wins and is installed with `--require-hashes`). The
plugin code is imported from its folder by `auoreo_plugin_rt`, which is copied into the
venv's site-packages. A matching lock (folder SHA-256 + python) skips the whole step.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from auoreo.core.secrets import child_env

RT_FILE = Path(__file__).with_name("rt") / "auoreo_plugin_rt.py"
HASH_EXCLUDE = {
    ".venv",
    "lock.json",
    "__pycache__",
    ".git",
    "pairing.json",
    "sessions.json",
    ".pytest_cache",
    "quarantine.json",  # H1b: the marker never changes what was verified
}
INSTALL_ENV = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "XDG_CACHE_HOME",
)
Verify = Literal["ok", "mismatch", "missing"]


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class Installed:
    name: str
    dir: Path
    python: Path
    lock: dict[str, Any]
    fresh: bool


def install_dir(home: Path, name: str) -> Path:
    return home / "plugins" / name


def folder_hash(folder: Path) -> str:
    """SHA-256 over sorted relative paths + contents, skipping venv/lock/cache/state files."""
    h = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder)
        if any(part in HASH_EXCLUDE for part in rel.parts) or path.suffix == ".pyc" or not path.is_file():
            continue
        h.update(rel.as_posix().encode() + b"\0")
        h.update(path.read_bytes() + b"\0")
    return h.hexdigest()


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def site_packages(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Lib" / "site-packages"
    found = sorted(venv.glob("lib/python*/site-packages"))
    if not found:
        raise InstallError(f"no site-packages under {venv}")
    return found[0]


def read_lock(home: Path, name: str) -> dict[str, Any] | None:
    path = install_dir(home, name) / "lock.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def verify(home: Path, name: str, src: Path) -> Verify:
    """Doctor check: does the installed lock still match the plugin folder?"""
    lock = read_lock(home, name)
    if lock is None or not venv_python(install_dir(home, name) / ".venv").exists():
        return "missing"
    return "ok" if lock.get("sha256") == folder_hash(src) else "mismatch"


def _pyproject(src: Path) -> dict[str, Any]:
    try:
        return tomllib.loads((src / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise InstallError(f"unreadable pyproject.toml: {exc}") from exc


def _install_env() -> dict[str, str]:
    env = child_env([], None)
    for key, value in os.environ.items():
        if key in INSTALL_ENV or key.startswith("UV_"):
            env[key] = value
    return env


async def _run(argv: list[str], cwd: Path, timeout: float) -> None:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        env=_install_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise InstallError(f"{argv[0]} {argv[1]} timed out after {timeout:g}s") from exc
    if proc.returncode != 0:
        tail = out.decode(errors="replace")[-1500:]
        raise InstallError(f"`{' '.join(argv[:3])} …` failed ({proc.returncode}): {tail}")


async def _make_venv(venv: Path, python: str, seed: bool, cwd: Path, timeout: float) -> None:
    uv = shutil.which("uv")
    if uv:
        await _run([uv, "venv", "--python", python, *(["--seed"] if seed else []), str(venv)], cwd, timeout)
    else:
        await _run([sys.executable, "-m", "venv", "--without-pip", str(venv)], cwd, timeout)


async def _install_deps(src: Path, venv: Path, timeout: float) -> list[str]:
    project = _pyproject(src)
    py = str(venv_python(venv))
    uv = shutil.which("uv")
    locked = src / "requirements.lock"
    if locked.is_file():
        argv = ["pip", "install", "--python", py, "--require-hashes", "-r", str(locked)]
    elif "build-system" in project:
        argv = ["pip", "install", "--python", py, str(src)]
    else:
        deps = [str(d) for d in project.get("project", {}).get("dependencies", [])]
        if not deps:
            return []
        argv = ["pip", "install", "--python", py, *deps]
    if uv is None:
        raise InstallError("installing plugin dependencies needs `uv` on PATH")
    await _run([uv, *argv], src, timeout)
    return argv[4:]


def copy_runtime(venv: Path) -> None:
    shutil.copyfile(RT_FILE, site_packages(venv) / "auoreo_plugin_rt.py")


async def install(
    name: str,
    src: Path,
    home: Path,
    *,
    python: str = "3.12",
    seed: bool = False,
    timeout: float = 600.0,
) -> Installed:
    """Create/refresh the venv when the lock is missing or stale; always refresh the runtime."""
    target = install_dir(home, name)
    target.mkdir(parents=True, exist_ok=True)
    venv = target / ".venv"
    sha = folder_hash(src)
    lock = read_lock(home, name)
    if lock and lock.get("sha256") == sha and lock.get("python") == python and venv_python(venv).exists():
        copy_runtime(venv)
        return Installed(name, target, venv_python(venv), lock, fresh=False)
    if venv.exists():
        shutil.rmtree(venv)
    await _make_venv(venv, python, seed, target, timeout)
    installed = await _install_deps(src, venv, timeout)
    copy_runtime(venv)
    lock = {
        "name": name,
        "version": _pyproject(src).get("project", {}).get("version", ""),
        "sha256": sha,
        "python": python,
        "requirements": installed,
        "installed_at": datetime.now(UTC).isoformat(),
    }
    tmp = target / "lock.json.tmp"
    tmp.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target / "lock.json")
    return Installed(name, target, venv_python(venv), lock, fresh=True)
