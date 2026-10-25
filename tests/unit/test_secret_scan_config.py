"""G5c: gitleaks wiring — config parses, allowlists stay scoped, CI + pre-commit run it."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_gitleaks_config_extends_defaults_and_allowlists_only_fixtures_and_generated() -> None:
    cfg = tomllib.loads((ROOT / ".gitleaks.toml").read_text())
    assert cfg["extend"]["useDefault"] is True
    paths = [re.compile(p) for a in cfg["allowlists"] for p in a.get("paths", [])]

    def allowed(path: str) -> bool:
        return any(p.search(path) for p in paths)

    assert allowed("tests/fixtures/cassettes/x/y.yaml") and allowed("tests/unit/test_redaction.py")
    assert allowed("uv.lock") and allowed("web/sdk/openapi.json")
    for scanned in ("src/auoreo/core/secrets.py", "tools/bash/handler.py", ".env", "settings.json"):
        assert not allowed(scanned), f"{scanned} must stay scanned"


def test_secret_scan_workflow_and_pre_commit_hook() -> None:
    wf = yaml.safe_load((ROOT / ".github/workflows/secret-scan.yml").read_text())
    steps = wf["jobs"]["gitleaks"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == 0
    assert any("--config .gitleaks.toml" in str(s.get("run", "")) for s in steps)
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"]
    assert any(r["repo"].endswith("gitleaks/gitleaks") and r["hooks"][0]["id"] == "gitleaks" for r in hooks)
