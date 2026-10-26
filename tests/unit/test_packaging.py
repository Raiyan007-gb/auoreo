"""W3-A packaging: Dockerfile/compose/release workflow structure, version guard, wheel contents,
PyInstaller inputs, manifest signing, and the frozen bundled root."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from auoreo.core import paths
from auoreo.core import upgrade as up

REPO = Path(__file__).resolve().parents[2]
SECRETISH = re.compile(r"(PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE)", re.I)


def _load(rel: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(Path(rel).stem, REPO / rel)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dockerfile() -> list[tuple[str, str]]:
    """(INSTRUCTION, args) with line continuations joined and comments dropped."""
    text = (REPO / "Dockerfile").read_text().replace("\\\n", " ")
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            word, _, rest = line.partition(" ")
            out.append((word.upper(), rest.strip()))
    return out


# ------------------------------------------------------------------ container
def test_dockerfile_structure() -> None:
    ins = _dockerfile()
    stages = [args.split()[-1] for word, args in ins if word == "FROM"]
    assert stages == ["web", "py", "runtime"]
    runtime = ins[max(i for i, (w, _) in enumerate(ins) if w == "FROM") :]
    users = [a for w, a in runtime if w == "USER"]
    assert users and users[-1] not in {"root", "0"}
    env = " ".join(a for w, a in runtime if w == "ENV")
    assert "AUOREO_HOME=/data" in env and "AUOREO_HOST=0.0.0.0" in env
    assert ("VOLUME", '["/data"]') in runtime and ("EXPOSE", "4096") in runtime
    health = [a for w, a in runtime if w == "HEALTHCHECK"]
    assert health and "/api/health" in health[0]
    assert ("ENTRYPOINT", '["auoreo", "serve"]') in runtime
    py = " ".join(a for w, a in ins if w in {"RUN", "ARG"})
    assert "uv sync --frozen --no-dev" in py and "--extra rtc" in py
    assert "npm run build" in py
    for word, args in ins:  # nothing secret is baked into the image
        if word in {"ENV", "ARG"}:
            assert not SECRETISH.search(args), args
    froms = [a for w, a in ins if w == "FROM"]
    assert all(":" in f.split()[0] for f in froms), "pin base image tags"


def test_dockerignore_and_compose() -> None:
    ignored = (REPO / ".dockerignore").read_text().split()
    for entry in (".env", ".venv", "web/node_modules", ".git", "secrets.db"):
        assert entry in ignored
    compose = yaml.safe_load((REPO / "compose.yaml").read_text())
    svc = compose["services"]["auoreo"]
    assert "${AUOREO_SERVER_PASSWORD:?" in svc["environment"]["AUOREO_SERVER_PASSWORD"]
    assert "auoreo-data:/data" in svc["volumes"]
    assert compose["services"]["redis"]["profiles"] == ["redis"]


# ------------------------------------------------------------------ release workflow
def _workflow() -> dict[str, Any]:
    doc = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())
    assert isinstance(doc, dict)
    return doc


def test_release_workflow_structure() -> None:
    wf = _workflow()
    on = wf.get("on", wf.get(True))  # PyYAML reads the bare `on` key as True
    assert on == {"push": {"tags": ["v*"]}}
    jobs = wf["jobs"]
    assert {"guard", "web", "python-dist", "pypi", "image", "binaries", "github-release"} <= set(jobs)
    guard = json.dumps(jobs["guard"])
    assert "scripts/check_version_tag.py" in guard
    pypi = jobs["pypi"]
    assert pypi["environment"] == "pypi" and pypi["permissions"] == {"id-token": "write"}
    assert any(s.get("uses", "").startswith("pypa/gh-action-pypi-publish") for s in pypi["steps"])
    assert "id-token" not in wf.get("permissions", {})  # only the publishing jobs get OIDC
    image = {s.get("uses", "").split("@")[0]: s for s in jobs["image"]["steps"]}
    assert image["docker/build-push-action"]["with"]["platforms"] == "linux/amd64,linux/arm64"
    assert "latest=auto" in image["docker/metadata-action"]["with"]["flavor"]
    oses = {m["os"] for m in jobs["binaries"]["strategy"]["matrix"]["include"]}
    assert oses == {"ubuntu-latest", "ubuntu-24.04-arm", "macos-14", "windows-latest"}
    rel = json.dumps(jobs["github-release"])
    assert "SHA256SUMS" in rel and "sign_manifest.py" in rel and "UPDATE_SIGNING_KEY" in rel


def test_signing_steps_are_conditional_on_secrets() -> None:
    steps = _workflow()["jobs"]["binaries"]["steps"]
    signing = [s for s in steps if re.search(r"codesign|notar|sign|Azure|certificate", s.get("name", ""))]
    assert len(signing) >= 4
    for s in signing:
        assert "!= ''" in s.get("if", ""), s["name"]


# ------------------------------------------------------------------ version guard
def test_version_guard(tmp_path: Path) -> None:
    mod = _load("scripts/check_version_tag.py")
    assert mod.check(f"v{up.__version__}") == []
    assert mod.check(f"refs/tags/v{up.__version__}") == []
    assert len(mod.check("v999.0.0")) == 2
    assert any("must look like" in p for p in mod.check(up.__version__))
    (tmp_path / "src" / "auoreo").mkdir(parents=True)
    (tmp_path / "src" / "auoreo" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "1.2.4"\n')
    assert mod.check("v1.2.3", tmp_path) == ["pyproject is '1.2.4' but the tag says '1.2.3'"]
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts/check_version_tag.py"), "v0.0.0-nope"], capture_output=True
    )
    assert r.returncode == 1


# ------------------------------------------------------------------ manifest signing
def test_sign_manifest_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load("packaging/sign_manifest.py")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    monkeypatch.setenv("UPDATE_SIGNING_KEY", pem.decode())
    assets = tmp_path / "bin"
    assets.mkdir()
    (assets / "auoreo-linux-x64").write_bytes(b"L")
    (assets / "auoreo-windows-x64.exe").write_bytes(b"W")
    written = mod.build("1.0.0", assets, "https://dl.test/v1.0.0", tmp_path / "m")
    assert [p.name for p in written] == ["manifest-stable.json", "manifest-beta.json", "manifest-dev.json"]
    pub = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    m = up.verify_manifest(json.loads(written[0].read_text()), [pub])
    assert m.min_schema_version == up.MIN_UPGRADE_SCHEMA
    assert m.assets["windows-x64"].url == "https://dl.test/v1.0.0/auoreo-windows-x64.exe"
    assert m.assets["linux-x64"].sha256 == hashlib.sha256(b"L").hexdigest()
    sums = (assets / "SHA256SUMS").read_text().splitlines()
    assert sums[0].endswith("  auoreo-linux-x64") and len(sums) == 2
    assert [p.name for p in mod.build("1.1.0rc1", assets, "u", tmp_path / "b")] == [
        "manifest-beta.json",
        "manifest-dev.json",
    ]


# ------------------------------------------------------------------ bundled root / PyInstaller
def test_bundled_root_frozen(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("AUOREO_BUNDLED_ROOT")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.bundled_root() == tmp_path
    assert paths.web_dist_dir() == tmp_path / "web" / "dist"


def test_bundled_root_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUOREO_BUNDLED_ROOT")
    assert paths.bundled_root() == REPO


def test_pyinstaller_inputs() -> None:
    collect = _load("packaging/pyinstaller/collect.py")
    dirs = collect.bundled_dirs(REPO)
    assert set(paths.KINDS) | {"policies", "memory", "connectors"} == set(dirs) == set(paths.BUNDLED_DIRS)
    dests = {d for _, d in collect.data_dirs(REPO)}
    assert {d for d in dirs if (REPO / d).is_dir()} <= dests
    hidden = collect.definition_imports(REPO)
    assert "httpx" in hidden and "playwright" not in hidden and "auoreo" not in hidden
    spec = (REPO / "packaging/pyinstaller/auoreo.spec").read_text()
    for needle in ("sqlite_vec", "core/alembic", 'collect_submodules("uvicorn")', "litellm", "entry.py"):
        assert needle in spec


# ------------------------------------------------------------------ wheel contents
def _tree(dst: Path, *, web: bool) -> Path:
    """A minimal copy of the project (source + definitions), optionally with a fake web/dist."""
    dst.mkdir()
    for name in ("pyproject.toml", "README.md", "alembic.ini", "uv.lock"):
        shutil.copy2(REPO / name, dst / name)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".venv", "node_modules", "_bundled")
    shutil.copytree(REPO / "src", dst / "src", ignore=ignore)
    shutil.copytree(REPO / "packaging", dst / "packaging", ignore=ignore)
    for d in paths.BUNDLED_DIRS:
        if (REPO / d).is_dir():
            shutil.copytree(REPO / d, dst / d, ignore=ignore)
    if web:
        (dst / "web" / "dist" / "assets").mkdir(parents=True)
        (dst / "web" / "dist" / "index.html").write_text("<!doctype html><title>auoreo</title>")
        (dst / "web" / "dist" / "assets" / "app.js").write_text("console.log(1)")
    return dst


def _uv_build(src: Path, out: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full = {k: v for k, v in os.environ.items() if k != "AUOREO_ALLOW_NO_WEB_DIST"}
    full.update(env or {})
    return subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out)],
        cwd=src,
        capture_output=True,
        text=True,
        env=full,
        timeout=300,
    )


needs_uv = pytest.mark.skipif(shutil.which("uv") is None, reason="uv not on PATH")


@needs_uv
def test_wheel_ships_bundled_definitions_and_web_dist(tmp_path: Path) -> None:
    src = _tree(tmp_path / "proj", web=True)
    r = _uv_build(src, tmp_path / "out")
    assert r.returncode == 0, r.stderr[-2000:]
    (whl,) = (tmp_path / "out").glob("auoreo-*.whl")
    names = set(zipfile.ZipFile(whl).namelist())
    for must in (
        "auoreo/_bundled/web/dist/index.html",
        "auoreo/_bundled/web/dist/assets/app.js",
        "auoreo/_bundled/agents/default/AGENT.md",
        "auoreo/_bundled/memory/MEMORY.md",
        "auoreo/_bundled/policies/default.star",
        "auoreo/core/alembic/versions/0001_baseline.py",
        "auoreo/core/alembic/script.py.mako",
        "auoreo/plugins/rt/auoreo_plugin_rt.py",
    ):
        assert must in names, must
    tools = {n.split("/")[3] for n in names if re.fullmatch(r"auoreo/_bundled/tools/[^/]+/tool\.yaml", n)}
    assert tools == {p.name for p in (REPO / "tools").iterdir() if (p / "tool.yaml").is_file()}
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")]


@needs_uv
def test_wheel_build_fails_without_web_dist(tmp_path: Path) -> None:
    src = _tree(tmp_path / "proj", web=False)
    r = _uv_build(src, tmp_path / "out")
    assert r.returncode != 0 and "web/dist/index.html is missing" in r.stderr
    r = _uv_build(src, tmp_path / "out2", {"AUOREO_ALLOW_NO_WEB_DIST": "1"})
    assert r.returncode == 0, r.stderr[-2000:]
    (whl,) = (tmp_path / "out2").glob("auoreo-*.whl")
    names = zipfile.ZipFile(whl).namelist()
    assert "auoreo/_bundled/agents/default/AGENT.md" in names
    assert not [n for n in names if "/web/dist/" in n]
