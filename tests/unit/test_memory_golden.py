"""I4 §25.8 L996: the generated memory golden set is current, large enough and gated."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

from tests.conftest import REPO


def _load(name: str) -> ModuleType:
    path = REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"auoreo_script_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_golden_is_current_and_well_formed() -> None:
    gen = _load("gen_memory_golden")
    assert gen.main(["--check"]) == 0
    rows = [json.loads(ln) for ln in (REPO / "evals/memory/golden.jsonl").read_text().splitlines()]
    facts = {json.loads(ln)["id"] for ln in (REPO / "evals/memory/facts.jsonl").read_text().splitlines()}
    assert len(rows) >= 200
    assert all({"query", "allowed_fact_ids", "as_of", "scope"} <= set(r) for r in rows)
    assert all(set(r["allowed_fact_ids"]) <= facts for r in rows)
    assert {r["category"] for r in rows} >= {"prefs", "conventions", "temporal", "contradiction", "scope"}


async def test_golden_meets_gate() -> None:
    script = _load("eval_memory")
    golden = REPO / "evals/memory/golden.jsonl"
    assert script.is_v2(golden)
    results = await script.run_golden_v2(golden, ["none"])
    gate = json.loads((REPO / "evals/memory/gate.json").read_text())
    gate.pop("p95_ms")  # latency is gated in CI only (machine dependent)
    assert script.gate_failures(results["none"], gate) == []
    worse = {**results["none"], "recall@10": 0.1}
    assert script.gate_failures(worse, gate)


def test_legacy_fixture_path_unchanged(tmp_path: Path) -> None:
    script = _load("eval_memory")
    assert not script.is_v2(REPO / "tests/fixtures/memory_golden.jsonl")
