"""G2b: spec §12 prompt assembly — toolOrder, complete sections, new sections, family overlays."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from auoreo.harness.discovery import Root, default_roots
from auoreo.harness.manifests import UNLISTED_TOOLS, VariantsFile
from auoreo.harness.orchestrator import build_spec, order_tools, pick_family, render_context
from auoreo.harness.registry import Harness, HarnessError
from tests.conftest import REPO, write


def bundled(tmp_path: Path) -> Harness:
    return Harness(default_roots(tmp_path / "h", tmp_path, REPO))


def test_variants_tool_order_needs_exactly_one_placeholder() -> None:
    assert VariantsFile().tool_order == [UNLISTED_TOOLS]
    assert VariantsFile.model_validate({"toolOrder": ["read", UNLISTED_TOOLS]}).tool_order[0] == "read"
    for bad in (["read"], [UNLISTED_TOOLS, UNLISTED_TOOLS], ["read", "read", UNLISTED_TOOLS]):
        with pytest.raises(ValidationError):
            VariantsFile.model_validate({"toolOrder": bad})


def test_order_tools_places_unlisted_at_the_placeholder(tmp_path: Path) -> None:
    h = bundled(tmp_path)
    tools = [h.tools[n] for n in ("bash", "glob", "grep", "read", "todo", "write")]
    out = order_tools(tools, ["read", "grep", UNLISTED_TOOLS, "bash", "missing"])
    assert [t.name for t in out] == ["read", "grep", "glob", "todo", "write", "bash"]
    assert order_tools(tools, ["read"]) == tools  # no placeholder: unchanged


def test_bundled_prompt_sections_order_and_tool_order(tmp_path: Path) -> None:
    h = bundled(tmp_path)
    assert h.errors == []
    assert {"plan", "team", "structured_output"} <= set(h.snapshot.prompts)
    orders = {n: h.snapshot.prompts[n].section.order for n in ("plan", "team", "structured_output")}
    assert orders == {"plan": 500, "team": 600, "structured_output": 9900}
    spec = build_spec(h, "default", "default", "manual")
    sp = spec.system_prompt
    marks = ["You are AUOREO", "# Safety", "# Planning", "# Delegation", "# Tools", "# Answer style"]
    pos = [sp.index(m) for m in marks] + [sp.index("# Structured output")]
    assert pos == sorted(pos)
    names = [t.name for t in spec.tools]
    assert names[:4] == ["read", "grep", "glob", "codebase_map"]
    assert names.index("bash") < names.index("todo")
    order = list(h.snapshot.variants.manifest.tool_order) if h.snapshot.variants else []
    assert order.count(UNLISTED_TOOLS) == 1
    assert build_spec(h, "default", "default", "manual").system_prompt == sp  # byte-stable


def test_families_cover_six_overlays_and_plan_reminder_is_mode_only(tmp_path: Path) -> None:
    h = bundled(tmp_path)
    fams = h.snapshot.families
    assert {"anthropic", "openai", "gemini", "kimi", "default", "plan_reminder"} <= set(fams)
    assert pick_family(fams, "openrouter/google/gemini-2.5-pro").family.name == "gemini"  # type: ignore[union-attr]
    assert pick_family(fams, "openrouter/moonshotai/kimi-k2").family.name == "kimi"  # type: ignore[union-attr]
    assert pick_family(fams, "openai/codex-mini").family.name == "openai"  # type: ignore[union-attr]
    assert pick_family(fams, "local/unknown").family.name == "default"  # type: ignore[union-attr]
    spec = build_spec(h, "default", "local/unknown", "manual")
    assert "<plan-reminder>" not in spec.system_prompt
    plan = render_context(spec, tmp_path, "plan", locale="")
    manual = render_context(spec, tmp_path, "manual", locale="")
    assert "<plan-reminder>" in plan and "<plan-reminder>" not in manual


def _complete(root: Path, name: str) -> None:
    write(root / "prompts" / f"{name}.md", f"---\nname: {name}\norder: 1\ncomplete: true\n---\nONLY {name}\n")


def test_complete_section_shadows_all_others(tmp_path: Path) -> None:
    overlay = tmp_path / "ov"
    _complete(overlay, "solo")
    h = Harness([Root("runtime", overlay), Root("bundled", REPO)])
    assert h.errors == []
    spec = build_spec(h, "default", "anthropic/claude-sonnet-5", "manual")
    assert spec.system_prompt == "ONLY solo"  # no other section, no family overlay
    assert spec.context_template  # context-tier sections are not system-prompt sections


def test_two_complete_sections_are_a_load_error(tmp_path: Path) -> None:
    overlay = tmp_path / "ov"
    _complete(overlay, "one")
    _complete(overlay, "two")
    h = Harness([Root("runtime", overlay), Root("bundled", REPO)])
    assert any("complete: true" in str(e) for e in h.errors)
    with pytest.raises(HarnessError):
        Harness([Root("runtime", overlay), Root("bundled", REPO)], strict_overlays=True)
