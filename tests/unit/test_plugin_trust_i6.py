"""I6 plugin trust tiers: ed25519 signatures, provenance + start gates, venv re-hash, taps,
allow-listed hash-pinned pip installs into the plugin's own venv, entry-point enumeration,
doctor rows (spec §25.15 L1076/L1077/L1081, §26.14 L1296)."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pytest_httpx import HTTPXMock

from auoreo.core.diagnostics import EXTRA_CHECKS
from auoreo.core.diagnostics_i6 import i6_checks
from auoreo.core.paths import bundled_root
from auoreo.core.settings import Settings
from auoreo.plugins import install as inst
from auoreo.plugins import quarantine, trust
from auoreo.plugins.host import PluginState
from auoreo.plugins.pip_install import TrustError, pip_add, resolve
from auoreo.plugins.registry import parse_index, tap_index
from auoreo.plugins.signing import (
    PLUGIN_PUBKEYS,
    key_id,
    public_b64,
    read_signatures,
    sign_entry,
    verify_entry,
    write_signatures,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "plugins" / "echo"
PY = f"{sys.version_info.major}.{sys.version_info.minor}"
SHA = "a" * 64


def settings_for(home: Path, **i6: Any) -> Settings:
    return Settings(home=home, gateway="fake", plugins_python=PY, i6=i6)


# ------------------------------------------------------------------ signatures
def test_sign_and_verify_entry_with_pinned_and_extra_keys() -> None:
    key = Ed25519PrivateKey.generate()
    entry = sign_entry("demo", "1.0.0", SHA, key)
    assert entry["signed_by"] == key_id(public_b64(key))
    assert not verify_entry("demo", "1.0.0", SHA, entry).ok  # not a pinned key
    ok = verify_entry("demo", "1.0.0", SHA, entry, [public_b64(key)])
    assert ok.ok and ok.signed_by == entry["signed_by"]
    assert verify_entry("demo", "1.0.0", "b" * 64, entry, [public_b64(key)]).reason.startswith(
        "content changed"
    )
    assert "version" in verify_entry("demo", "2.0.0", SHA, entry, [public_b64(key)]).reason
    assert not verify_entry("other", "1.0.0", SHA, entry, [public_b64(key)]).ok  # name is signed too
    assert verify_entry("demo", "1.0.0", SHA, None).reason == "unsigned"
    assert not verify_entry("demo", "1.0.0", SHA, {**entry, "sig": "%%%"}, [public_b64(key)]).ok
    assert verify_entry("demo", "1.0.0", SHA, entry, ["not-base64!", public_b64(key)]).ok  # junk key ignored


def test_doctor_hook_registered() -> None:
    assert i6_checks in EXTRA_CHECKS


def test_committed_signatures_cover_every_bundled_plugin() -> None:
    plugins = bundled_root() / "plugins"
    rows = read_signatures(plugins)
    names = {p.name for p in plugins.iterdir() if (p / "plugin.yaml").is_file()}
    assert set(rows) == names, "re-run `python packaging/sign_plugins.py sign` after adding a plugin"
    pinned = {key_id(k) for k in PLUGIN_PUBKEYS}
    for row in rows.values():
        assert row["signed_by"] in pinned and len(base64.b64decode(row["sig"])) == 64


def _bundled_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Ed25519PrivateKey]:
    """A fake bundled root holding the echo fixture, signed by a test key."""
    root = tmp_path / "bundled"
    shutil.copytree(FIXTURE, root / "plugins" / "echo")
    key = Ed25519PrivateKey.generate()
    folder = root / "plugins" / "echo"
    write_signatures(root / "plugins", {"echo": sign_entry("echo", "0.1.0", inst.folder_hash(folder), key)})
    monkeypatch.setattr("auoreo.core.paths.bundled_root", lambda: root)
    return folder, key


def _state(src: Path, version: str = "0.1.0") -> PluginState:
    return PluginState("echo", SimpleNamespace(version=version, provides=["tools"]), src)  # type: ignore[arg-type]


class FakeHost:
    def __init__(self, st: Settings) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.audits: list[dict[str, Any]] = []

        async def emit(scope: str, kind: str, data: dict[str, Any]) -> None:
            self.events.append((kind, data))

        async def record(**kw: Any) -> None:
            self.audits.append(kw)

        self.s = SimpleNamespace(settings=st, emit=emit, audit=SimpleNamespace(record=record))
        self.home = st.home


async def test_bundled_signature_gate_enforce_warn(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, key = _bundled_copy(tmp_path, monkeypatch)
    extra = [public_b64(key)]
    st = settings_for(home, plugins={"bundled_signatures": "enforce", "extra_pubkeys": extra})
    prov = trust.provenance(st, "echo", folder, "0.1.0")
    assert prov["tier"] == "bundled" and prov["signed"] and prov["signed_by"] == key_id(extra[0])
    host = FakeHost(st)
    ps = _state(folder)
    assert await trust.pre_start(host, ps) is not None  # type: ignore[arg-type]
    assert trust.row_extra(ps) == {"tier": "bundled", "signed_by": key_id(extra[0])}

    (folder / "plugin.py").write_text("# tampered\n", encoding="utf-8")
    ps = _state(folder)
    assert await trust.pre_start(host, ps) is None  # type: ignore[arg-type]
    assert ps.status == "unverified" and "content changed" in str(ps.error)
    assert host.events[-1][0] == "plugin.unverified"
    warn = settings_for(home, plugins={"bundled_signatures": "warn", "extra_pubkeys": extra})
    assert await trust.pre_start(FakeHost(warn), _state(folder)) is not None  # type: ignore[arg-type]
    rows = trust_rows = [c for c in await i6_checks(warn) if c.check == "plugin-signatures"]
    assert trust_rows and rows[0].level == "warn" and "echo" in rows[0].detail


def test_tiers_admit_and_refusals(home: Path) -> None:
    assert trust.classify("https://x/y.git") == "url" and trust.classify("git@github.com:a/b") == "url"
    assert trust.classify("./folder") == "local-path"
    assert trust.admit("url", trust_local=True) is not None
    assert trust.admit("local-path", trust_local=False) is not None
    assert trust.admit("local-path", trust_local=True) is None
    assert trust.TIER_RANK["bundled"] < trust.TIER_RANK["allow-listed"] < trust.TIER_RANK["local-path"]
    pkg = {"name": "auoreo-plugin-demo", "version": "1.0.0", "sha256": SHA}
    st = settings_for(
        home, plugins={"allow_list": {"auoreo-plugin-demo": {"version": "1.0.0", "sha256": SHA}}}
    )
    assert trust.refusal(st, {"tier": "allow-listed", "registry": "allow-list", "package": pkg}) is None
    repinned = settings_for(
        home, plugins={"allow_list": {"auoreo-plugin-demo": {"version": "1.0.1", "sha256": SHA}}}
    )
    assert "no longer allow-listed" in str(
        trust.refusal(repinned, {"tier": "allow-listed", "registry": "allow-list", "package": pkg})
    )
    tap = {"tier": "allow-listed", "registry": "https://tap/i.json", "package": pkg}
    assert "no longer configured" in str(trust.refusal(st, tap))
    assert trust.refusal(settings_for(home, plugins={"taps": ["https://tap/i.json"]}), tap) is None
    assert trust.refusal(st, {"tier": "url"}) is not None


# ------------------------------------------------------------------ venv integrity
def _fake_venv(base: Path) -> Path:
    venv = base / ".venv"
    sp = venv / "lib" / f"python{PY}" / "site-packages"
    (sp / "demo").mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "bin" / "python").write_text("", encoding="utf-8")
    code = b"print('hi')\n"
    (sp / "demo" / "__init__.py").write_bytes(code)
    digest = base64.urlsafe_b64encode(hashlib.sha256(code).digest()).rstrip(b"=").decode()
    di = sp / "demo-1.0.dist-info"
    di.mkdir()
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: demo\nVersion: 1.0\n", encoding="utf-8")
    (di / "entry_points.txt").write_text("[auoreo.plugin]\ndemo = demo:Plugin\n", encoding="utf-8")
    (di / "RECORD").write_text(
        f"demo/__init__.py,sha256={digest},{len(code)}\n../../../bin/x,,\n", encoding="utf-8"
    )
    return venv


def test_venv_hash_records_and_installed_problem(home: Path) -> None:
    target = inst.install_dir(home, "demo")
    venv = _fake_venv(target)
    lock = {"name": "demo", "sha256": "x", "venv_sha256": trust.venv_hash(venv), "entry": "ep:demo:Plugin"}
    (target / "lock.json").write_text(json.dumps(lock), encoding="utf-8")
    assert trust.record_problems(venv) == [] and trust.installed_problem(home, "demo") is None
    eps = trust.venv_entry_points(venv)
    assert eps == [
        {"name": "demo", "value": "demo:Plugin", "group": "auoreo.plugin", "dist": "demo", "version": "1.0"}
    ]
    sp = inst.site_packages(venv)
    (sp / "demo" / "__init__.py").write_text("import os; os.system('evil')\n", encoding="utf-8")
    assert "hash differs" in str(trust.installed_problem(home, "demo"))
    (sp / "demo-1.0.dist-info" / "RECORD").write_text("", encoding="utf-8")  # cover the tracks
    assert "venv_sha256 mismatch" in str(trust.installed_problem(home, "demo"))


async def test_post_install_quarantines_a_tampered_user_plugin(home: Path) -> None:
    target = inst.install_dir(home, "echo")
    shutil.copytree(FIXTURE, target)
    venv = _fake_venv(target)
    lock = {"name": "echo", "sha256": inst.folder_hash(target), "venv_sha256": "0" * 64, "tier": "local-path"}
    (target / "lock.json").write_text(json.dumps(lock), encoding="utf-8")
    assert venv.is_dir()
    host = FakeHost(settings_for(home))
    ps = _state(target)
    ps.trust = {"tier": "local-path"}
    assert await trust.post_install(host, ps) is False  # type: ignore[arg-type]
    assert ps.status == "quarantined" and quarantine.is_quarantined(home, "echo")
    assert host.audits[-1]["action"] == "plugin.quarantine" and host.events[-1][0] == "plugin.quarantined"
    rows = {c.check: c for c in await i6_checks(settings_for(home))}
    assert rows["plugin-trust:echo"].level == "error" and "venv_sha256" in rows["plugin-trust:echo"].detail


def test_entry_points_of_this_environment_are_listed_not_imported(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.metadata as md

    ep = md.EntryPoint("demo", "would_explode_if_imported:Plugin", "auoreo.plugin")
    monkeypatch.setattr(trust.md, "entry_points", lambda group: [ep] if group == "auoreo.plugin" else [])
    rows = trust.entry_point_plugins(["auoreo.plugin", "oreo.plugin"])
    assert rows == [
        {
            "name": "demo",
            "value": "would_explode_if_imported:Plugin",
            "group": "auoreo.plugin",
            "dist": None,
            "version": None,
            "status": "quarantined",
            "tier": "allow-listed",
        }
    ]
    assert "would_explode_if_imported" not in sys.modules


# ------------------------------------------------------------------ taps + resolve
def test_parse_index_drops_unpinned_and_foreign_packages() -> None:
    body = {
        "plugins": [
            {"package": "auoreo-plugin-ok", "version": "1.0.0", "sha256": SHA},
            {"package": "auoreo-plugin-nopin", "version": "1.0.0"},
            {"package": "requests", "version": "2.0", "sha256": SHA},
            "junk",
        ]
    }
    entries = parse_index("t", body)
    assert [(e.package, e.name) for e in entries] == [("auoreo-plugin-ok", "ok")]


async def test_tap_index_cache_and_offline_fallback(home: Path, httpx_mock: HTTPXMock) -> None:
    import httpx

    tap = "https://tap.example/index.json"
    st = settings_for(home, plugins={"taps": [tap], "index_ttl_s": 0})
    httpx_mock.add_response(
        url=tap, json={"plugins": [{"package": "oreo-plugin-x", "version": "1.0.0", "sha256": SHA}]}
    )
    entries, err = await tap_index(st, tap)
    assert err is None and entries[0].package == "oreo-plugin-x"
    httpx_mock.add_exception(httpx.ConnectError("offline"), url=tap)
    entries, err = await tap_index(st, tap)
    assert entries[0].package == "oreo-plugin-x" and err is not None and "cached index" in err


async def test_resolve_allow_list_tap_and_signatures(home: Path, httpx_mock: HTTPXMock) -> None:
    allowed = settings_for(
        home, plugins={"allow_list": {"auoreo-plugin-demo": {"version": "1.0.0", "sha256": SHA}}}
    )
    r = await resolve(allowed, "auoreo-plugin-demo==1.0.0")
    assert (r.registry, r.name, r.lock_lines()) == (
        "allow-list",
        "demo",
        [f"auoreo-plugin-demo==1.0.0 --hash=sha256:{SHA}"],
    )
    for bad in (
        "requests",
        "auoreo-plugin-demo==9.9.9",
        "https://x/auoreo-plugin-demo.whl",
        "Auoreo-Plugin-X;rm",
    ):
        with pytest.raises(TrustError):
            await resolve(allowed, bad)

    tap = "https://tap.example/index.json"
    key = Ed25519PrivateKey.generate()
    signed = sign_entry("auoreo-plugin-weather", "2.0.0", SHA, key)
    row = {"package": "auoreo-plugin-weather", "version": "2.0.0", "sha256": SHA, **signed}
    httpx_mock.add_response(url=tap, json={"plugins": [row]}, is_reusable=True)
    st = settings_for(home, plugins={"taps": [tap], "extra_pubkeys": [public_b64(key)]})
    r = await resolve(st, "auoreo-plugin-weather")
    assert r.registry == tap and r.signed_by == key_id(public_b64(key))
    untrusted = settings_for(home, plugins={"taps": [tap], "index_ttl_s": 0})
    with pytest.raises(TrustError, match="signature"):
        await resolve(untrusted, "auoreo-plugin-weather")


# ------------------------------------------------------------------ pip-add end to end
PLUGIN_PY = """
class Plugin:
    def __init__(self, host):
        self.host = host

    def initialize(self, config, consumes_granted):
        return {"tools": [{"name": "hello", "description": "Say hello."}]}

    def call_tool(self, name, args, session_id):
        return "hello from a pip plugin"
"""
PLUGIN_YAML = "name: ignored\nversion: 0.0.0\ndescription: Demo pip plugin.\nprovides: [tools]\n"


def build_wheel(out: Path) -> Path:
    """A minimal valid wheel for `auoreo-plugin-demo` 1.0.0 with an `auoreo.plugin` entry point."""
    files = {
        "auoreo_plugin_demo/__init__.py": PLUGIN_PY.encode(),
        "auoreo_plugin_demo/plugin.yaml": PLUGIN_YAML.encode(),
        "auoreo_plugin_demo-1.0.0.dist-info/METADATA": (
            b"Metadata-Version: 2.1\nName: auoreo-plugin-demo\nVersion: 1.0.0\nSummary: Demo pip plugin.\n"
        ),
        "auoreo_plugin_demo-1.0.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ),
        "auoreo_plugin_demo-1.0.0.dist-info/entry_points.txt": (
            b"[auoreo.plugin]\ndemo = auoreo_plugin_demo:Plugin\n"
        ),
    }
    record = []
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(data)}")
    record.append("auoreo_plugin_demo-1.0.0.dist-info/RECORD,,")
    files["auoreo_plugin_demo-1.0.0.dist-info/RECORD"] = ("\n".join(record) + "\n").encode()
    out.mkdir(parents=True, exist_ok=True)
    wheel = out / "auoreo_plugin_demo-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return wheel


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
async def test_pip_add_installs_pinned_wheel_into_its_own_venv_quarantined(
    tmp_path: Path, home: Path
) -> None:
    wheel = build_wheel(tmp_path / "dist")
    sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    pin = {"version": "1.0.0", "sha256": sha, "url": wheel.as_uri()}
    st = settings_for(home, plugins={"allow_list": {"auoreo-plugin-demo": pin}})
    res = await pip_add(st, "auoreo-plugin-demo==1.0.0")
    assert (
        res["name"] == "demo"
        and res["entry"] == "auoreo_plugin_demo:Plugin"
        and res["registry"] == "allow-list"
    )
    target = inst.install_dir(home, "demo")
    lock = inst.read_lock(home, "demo") or {}
    assert lock["tier"] == "allow-listed" and lock["registry"] == "allow-list" and lock["signed_by"] is None
    assert lock["package"] == {"name": "auoreo-plugin-demo", "version": "1.0.0", "sha256": sha}
    assert lock["sha256"] == inst.folder_hash(target) and lock["venv_sha256"] == trust.venv_hash(
        target / ".venv"
    )
    assert "--hash=sha256:" + sha in (target / "requirements.lock").read_text()
    manifest = (target / "plugin.yaml").read_text()
    assert "entry: auoreo_plugin_demo:Plugin" in manifest and "name: demo" in manifest
    assert "auoreo_plugin_demo" not in sys.modules  # enumerated from metadata, never imported here
    assert quarantine.is_quarantined(home, "demo")
    assert trust.installed_problem(home, "demo") is None
    report = quarantine.verify(home, "demo", st)
    assert report.ok, report.problems
    assert quarantine.enable(home, "demo") is None
    prov = trust.provenance(st, "demo", target, "1.0.0")
    assert prov["tier"] == "allow-listed" and trust.refusal(st, prov) is None
    assert trust.refusal(settings_for(home), prov) is not None  # pin removed from the allow-list

    # Wrong pin: uv refuses, nothing is left behind.
    bad = settings_for(home, plugins={"allow_list": {"auoreo-plugin-demo": {**pin, "sha256": "0" * 64}}})
    with pytest.raises(inst.InstallError):
        await pip_add(bad, "auoreo-plugin-demo", force=True)
    assert not target.exists()


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
async def test_pip_plugin_runs_from_its_module_entry_point(tmp_path: Path, home: Path, workdir: Path) -> None:
    from auoreo.core.permissions import PermissionEngine
    from auoreo.core.services import Services
    from auoreo.gateway.fake import FakeGateway
    from auoreo.gateway.types import ToolCall
    from auoreo.harness.manifests import Rule
    from auoreo.tools.context import ToolContext

    wheel = build_wheel(tmp_path / "dist")
    pin = {
        "version": "1.0.0",
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "url": wheel.as_uri(),
    }
    st = Settings(
        home=home,
        gateway="fake",
        plugins_python=PY,
        plugins_backoff_scale=0.0,
        i6={"plugins": {"allow_list": {"auoreo-plugin-demo": pin}}},
    )
    await pip_add(st, "auoreo-plugin-demo")
    assert quarantine.verify(home, "demo", st).ok and quarantine.enable(home, "demo") is None
    svc = await Services.create(st, cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False)
    try:
        row = next(r for r in svc.ext["plugins"].rows() if r["name"] == "demo")
        assert row["status"] == "running", row
        assert row["tier"] == "allow-listed"
        ctx = ToolContext(session_id="s", cwd=workdir, home=home)
        engine = PermissionEngine([Rule(permission="*", pattern="*", action="allow")])
        out = await svc.coordinator.runner.executor.run(
            ToolCall("c1", "plugin__demo__hello", "{}"), tools=svc.harness.tools, ctx=ctx, engine=engine
        )
        assert out.result.content == "hello from a pip plugin"
    finally:
        await svc.close()
