"""H1b: `plugin add` (local needs --trust-local) lands quarantined; the host never starts it
until `plugin verify` + `plugin enable`; each step is audited (spec §25.15)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from auoreo.cli.app import app as cli_app
from auoreo.core.diagnostics_h1b import h1b_checks
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway
from auoreo.plugins import quarantine

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "plugins" / "echo"
PY = f"{sys.version_info.major}.{sys.version_info.minor}"
ENV = {"AUOREO_PORT": "9"}  # nothing listens → on-disk paths
runner = CliRunner()


def _actions(home: Path) -> list[tuple[str, str]]:
    con = sqlite3.connect(home / "state.db")
    try:
        return [(a, d) for a, d in con.execute("SELECT action, decision FROM audit ORDER BY seq")]
    finally:
        con.close()


async def test_host_skips_a_quarantined_plugin(home: Path, workdir: Path) -> None:
    shutil.copytree(FIXTURE, home / "plugins" / "echo")
    quarantine.quarantine(home, "echo", source=str(FIXTURE), tier="local-path")
    st = Settings(home=home, gateway="fake", plugins_python=PY, plugins_backoff_scale=0.0)
    svc = await Services.create(st, cwd=workdir, gateway=FakeGateway(), with_mcp=False, with_memory=False)
    try:
        host = svc.ext["plugins"]
        assert host.plugins["echo"].status == "disabled"
        assert "echo" not in {t for t in svc.harness.tools if t.startswith("echo")}
        rows = {c.check: c for c in await h1b_checks(st)}
        assert (
            rows["plugin_quarantine:echo"].level == "warn"
            and "verify" in rows["plugin_quarantine:echo"].remedy
        )
    finally:
        await svc.close()


def test_cli_flow_tamper_detection_and_audit(home: Path, tmp_path: Path) -> None:
    assert (
        runner.invoke(
            cli_app, ["plugin", "add", "https://evil.example/p.git", "--trust-local"], env=ENV
        ).exit_code
        == 2
    )
    refused = runner.invoke(cli_app, ["plugin", "add", str(FIXTURE)], env=ENV)
    assert refused.exit_code == 2 and "--trust-local" in refused.output
    assert runner.invoke(cli_app, ["plugin", "add", str(FIXTURE), "--trust-local"], env=ENV).exit_code == 0
    marker = json.loads((home / "plugins" / "echo" / "quarantine.json").read_text())
    assert marker["tier"] == "local-path" and marker["verified"] is None
    # tampering after add is caught by verify
    plugin_py = home / "plugins" / "echo" / "plugin.py"
    original = plugin_py.read_text()
    plugin_py.write_text(original + "\n# injected\n")
    r = runner.invoke(cli_app, ["plugin", "verify", "echo"], env=ENV)
    assert r.exit_code == 1 and "hash mismatch" in r.output
    plugin_py.write_text(original)
    assert runner.invoke(cli_app, ["plugin", "verify", "echo"], env=ENV).exit_code == 0
    # a change after verification blocks enable
    plugin_py.write_text(original + "\n# later\n")
    r = runner.invoke(cli_app, ["plugin", "enable", "echo"], env=ENV)
    assert r.exit_code == 1 and "changed after it was verified" in r.output
    plugin_py.write_text(original)
    assert runner.invoke(cli_app, ["plugin", "enable", "echo"], env=ENV).exit_code == 0
    assert not quarantine.is_quarantined(home, "echo")
    acts = _actions(home)
    assert ("plugin.add", "allow") in acts and ("plugin.verify", "deny") in acts
    assert (
        ("plugin.verify", "allow") in acts
        and ("plugin.enable", "deny") in acts
        and ("plugin.enable", "allow") in acts
    )


@pytest.mark.parametrize("name", ["quarantine.json"])
def test_marker_is_outside_the_folder_hash(home: Path, name: str) -> None:
    from auoreo.plugins.install import folder_hash

    dest = home / "plugins" / "echo"
    shutil.copytree(FIXTURE, dest)
    before = folder_hash(dest)
    (dest / name).write_text("{}")
    assert folder_hash(dest) == before
