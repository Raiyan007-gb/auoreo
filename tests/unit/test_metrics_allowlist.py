"""Every `obs.count(...)` under src/auoreo names an allowlisted metric with allowlisted labels.

`observability.count` silently drops a metric that is not in `METRICS` (and any label key
the metric does not permit), so an unregistered name is invisible in OTel and `/metrics`
with no error. Names come from string literals or module-level string constants; a metric
may be registered in `observability.METRICS` or by `obs.METRICS.setdefault(...)` in the
module that emits it (the `gateway/budgets.py` precedent).
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from auoreo.core import metrics_text
from auoreo.core import observability as obs

SRC = Path(__file__).resolve().parents[2] / "src" / "auoreo"


def _module_name(path: Path) -> str:
    return ".".join(("auoreo", *path.relative_to(SRC).with_suffix("").parts))


def _constants(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)):
            continue
        if isinstance(node.value.value, str):
            out |= {t.id: node.value.value for t in node.targets if isinstance(t, ast.Name)}
    return out


def _count_calls() -> list[tuple[str, str, set[str]]]:
    """(module, metric name, label keys) for every `obs.count(...)` / `count(...)` in observability."""
    calls: list[tuple[str, str, set[str]]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts = _constants(tree)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            func = node.func
            if func.attr != "count" or not (isinstance(func.value, ast.Name) and func.value.id == "obs"):
                continue
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                name = first.value
            elif isinstance(first, ast.Name) and first.id in consts:
                name = consts[first.id]
            else:
                where = f"{path.relative_to(SRC)}:{node.lineno}"
                raise AssertionError(f"{where}: obs.count name must be a literal or constant")
            labels = {kw.arg for kw in node.keywords if kw.arg is not None}
            calls.append((_module_name(path), name, labels))
    return calls


def test_every_counted_metric_is_allowlisted_with_its_labels() -> None:
    calls = _count_calls()
    assert calls, "no obs.count calls found: the walk is broken"
    for module, _, _ in calls:
        importlib.import_module(module)  # module-level METRICS.setdefault registrations run here
    problems = []
    for module, name, labels in calls:
        spec = obs.METRICS.get(name)
        if spec is None:
            problems.append(f"{module}: '{name}' is not in observability.METRICS (silently dropped)")
            continue
        if extra := labels - set(spec[1]):
            problems.append(f"{module}: '{name}' labels {sorted(extra)} are not allowlisted (dropped)")
    assert not problems, "\n".join(problems)


def test_every_allowlisted_metric_has_help_text() -> None:
    for module, _, _ in _count_calls():
        importlib.import_module(module)
    missing = sorted(name for name in obs.METRICS if name not in metrics_text.HELP)
    assert not missing, f"metrics without a /metrics HELP line: {missing}"


def test_compaction_metrics_reach_the_text_exposition() -> None:
    importlib.import_module("auoreo.runtime.compaction")
    before = metrics_text.value("compaction_tokens_reclaimed_total")
    obs.count("compaction_tokens_reclaimed_total", 1234)
    obs.count("compaction_completed_total", trigger="manual", source="summary")
    obs.count("compaction_failures_total", trigger="auto", failure_class="timeout")
    assert metrics_text.value("compaction_tokens_reclaimed_total") == before + 1234
    assert metrics_text.value("compaction_completed_total", source="summary", trigger="manual") >= 1
    assert metrics_text.value("compaction_failures_total", failure_class="timeout", trigger="auto") >= 1
