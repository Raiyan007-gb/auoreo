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

    assert allowed("tests/unit/test_redaction.py")
    # I9: cassettes are scanned; a deliberate finding is allowed by fingerprint (.gitleaksignore)
    assert not allowed("tests/fixtures/cassettes/openai/stream_text.yaml")
    assert (ROOT / ".gitleaksignore").is_file()
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


def test_gitleaks_rules_cover_the_spec_prefixes() -> None:
    """I9 (§19.10/§25.14): one [[rules]] entry per vendor prefix; each regex matches a sample."""
    cfg = tomllib.loads((ROOT / ".gitleaks.toml").read_text())
    rules = {r["id"]: r for r in cfg["rules"]}
    samples = {
        "auoreo-openrouter-key": "sk-or-v1-" + "a1" * 16,
        "auoreo-anthropic-key": "sk-ant-api03-" + "Zz" * 12,
        "auoreo-perplexity-key": "pplx-" + "q" * 24,
        "auoreo-exa-key": "exa_" + "k" * 24,
        "auoreo-groq-key": "gsk_" + "g" * 24,
        "auoreo-tavily-key": "tvly-dev-" + "t" * 24,
        "auoreo-huggingface-token": "hf_" + "h" * 34,
        "auoreo-xai-key": "xai-" + "x" * 24,
        "auoreo-fireworks-key": "fw-" + "f" * 24,
        "auoreo-firecrawl-key": "fc-" + "0a" * 16,
        "auoreo-jina-key": "jina_" + "j" * 32,
        "auoreo-fernet-token": "gAAAAA" + "B" * 70,
        "auoreo-generic-llm-key": "dsk-" + "d" * 30,
    }
    for rule_id, sample in samples.items():
        rule = rules[rule_id]
        assert re.search(rule["regex"], f"key = {sample}"), rule_id
        assert any(k in sample for k in rule["keywords"]), rule_id
    assert not re.search(rules["auoreo-generic-llm-key"]["regex"], "litellm-proxy-config-value-long-enough")
