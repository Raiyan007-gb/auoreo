from __future__ import annotations

import sys
from pathlib import Path

import pytest

from auoreo.harness.discovery import Root, default_roots
from auoreo.harness.orchestrator import TemplateError, build_spec, render
from auoreo.harness.registry import Harness, HarnessError
from tests.conftest import REPO, make_tool, write


def roots(*paths: tuple[str, Path]) -> list[Root]:
    return [Root(src, p) for src, p in paths]


def test_bundled_definitions_load_and_handlers_import(tmp_path: Path) -> None:
    h = Harness(default_roots(tmp_path / "h", tmp_path, REPO))
    assert h.errors == []
    assert {"bash", "read", "write", "edit", "web_fetch"} <= set(h.tools)
    assert "default" in h.agents and "chat" in h.workflows
    for tool in h.tools.values():
        assert callable(tool.handler()), tool.name


def test_handler_import_is_lazy(tmp_path: Path) -> None:
    make_tool(tmp_path, "lazy_probe")
    h = Harness(roots(("bundled", tmp_path)))
    assert "auoreo_tool_lazy_probe" not in sys.modules
    h.tools["lazy_probe"].handler()
    assert "auoreo_tool_lazy_probe" in sys.modules


def test_overlay_error_is_skipped_but_bundled_error_is_fatal(tmp_path: Path) -> None:
    overlay, bundled = tmp_path / "overlay", tmp_path / "bundled"
    write(overlay / "tools" / "bad" / "tool.yaml", "name: bad\ndescription: x\nbogus_key: 1\n")
    write(overlay / "tools" / "bad" / "handler.py", "async def run(a, c): return ''\n")
    make_tool(bundled, "good")
    h = Harness(roots(("runtime", overlay), ("bundled", bundled)))
    assert "good" in h.tools and "bad" not in h.tools
    assert "bogus_key" in str(h.errors[0]) and ":3" in str(h.errors[0])  # file:line
    with pytest.raises(HarnessError):
        Harness(roots(("runtime", overlay), ("bundled", bundled)), strict_overlays=True)
    with pytest.raises(HarnessError):
        Harness(roots(("bundled", overlay)))


@pytest.mark.parametrize(
    ("files", "needle"),
    [
        ({"tool.yaml": "name: other\ndescription: x\n", "handler.py": ""}, "must match"),
        ({"tool.yaml": "name: t\ndescription: x\n"}, "handler file"),
        (
            {"tool.yaml": "name: t\ndescription: x\nparameters: {type: string}\n", "handler.py": ""},
            "type: object",
        ),
        ({"tool.yaml": "name: t\n  description: [\n", "handler.py": ""}, "invalid YAML"),
        (
            {"tool.yaml": "name: t\ndescription: x\nhandler: ../evil.py:run\n", "handler.py": ""},
            "handler must",
        ),
    ],
)
def test_tool_manifest_errors(tmp_path: Path, files: dict[str, str], needle: str) -> None:
    for name, text in files.items():
        write(tmp_path / "tools" / "t" / name, text)
    h = Harness(roots(("runtime", tmp_path)))
    assert "t" not in h.tools
    assert needle in str(h.errors[0])


def test_tool_shadowing_requires_override(tmp_path: Path) -> None:
    overlay, bundled = tmp_path / "o", tmp_path / "b"
    make_tool(bundled, "dup", "return 'bundled'")
    make_tool(overlay, "dup", "return 'overlay'")
    h = Harness(roots(("runtime", overlay), ("bundled", bundled)))
    assert h.tools["dup"].source == "bundled"
    assert "override: true" in str(h.errors[0])
    make_tool(overlay, "dup", "return 'overlay'", extra="override: true\n")
    h2 = Harness(roots(("runtime", overlay), ("bundled", bundled)))
    assert h2.tools["dup"].source == "runtime" and h2.errors == []


def test_agent_overlay_shadows_bundled_and_is_recorded(tmp_path: Path) -> None:
    write(
        tmp_path / "agents" / "default" / "AGENT.md",
        "---\nname: default\ndescription: overlay agent\n---\nOverlay body\n",
    )
    h = Harness(roots(("project", tmp_path), ("bundled", REPO)))
    assert h.agents["default"].manifest.description == "overlay agent"
    assert any(s.kind == "agents" and s.name == "default" for s in h.snapshot.shadowed)


def test_reload_picks_up_new_folder_and_bumps_generation(tmp_path: Path) -> None:
    runtime = tmp_path / "rt"
    h = Harness(roots(("runtime", runtime), ("bundled", REPO)))
    gen = h.generation
    make_tool(runtime, "hello")
    diff = h.reload()
    assert "hello" in h.tools and diff["tools"]["added"] == ["hello"]
    assert h.generation == gen + 1


def test_select_tools_globs_and_exposure(tmp_path: Path) -> None:
    make_tool(tmp_path, "aa_one")
    make_tool(tmp_path, "aa_two", extra="exposure: deferred\n")
    make_tool(tmp_path, "bb_hidden", extra="exposure: hidden\n")
    write(
        tmp_path / "agents" / "x" / "AGENT.md",
        "---\nname: x\ndescription: d\ntools: {allow: ['aa_*', 'bb_*'], deny: ['aa_one']}\n---\nbody\n",
    )
    h = Harness(roots(("bundled", tmp_path), ("extra:0", REPO)))
    names = [t.name for t in h.select_tools(h.agent("x").manifest)]
    assert names == ["aa_two"]
    spec = build_spec(h, "x", "openrouter/openai/gpt-4o-mini", "manual")
    assert [t.name for t in spec.tools] == [] and [t.name for t in spec.deferred] == ["aa_two"]


def test_system_prompt_is_assembled_from_sections_and_family(tmp_path: Path) -> None:
    h = Harness(default_roots(tmp_path / "h", tmp_path, REPO))
    spec = build_spec(h, "default", "openrouter/anthropic/claude-x", "manual")
    sp = spec.system_prompt
    assert sp.index("You are AUOREO") < sp.index("# Safety") < sp.index("# Tools")
    assert "`read`" in sp and "in parallel" in sp  # tool list + anthropic family overlay
    assert "{{" not in sp and "{{cwd}}" in spec.context_template


def test_unknown_prompt_variable_is_a_load_error(tmp_path: Path) -> None:
    write(tmp_path / "prompts" / "x.md", "---\nname: x\norder: 1\n---\nHello {{nope}}\n")
    h = Harness(roots(("runtime", tmp_path), ("bundled", REPO)))
    assert "unknown template variable" in str(h.errors[0])


def test_render_is_strict() -> None:
    assert render("{{a}}-{{b.c}}", {"a": 1, "b": {"c": 2}}) == "1-2"
    with pytest.raises(TemplateError):
        render("{{missing}}", {})


def test_workflow_validation(tmp_path: Path) -> None:
    write(
        tmp_path / "workflows" / "w.yaml",
        "name: w\ndescription: d\nsteps:\n  - {id: a, agent: default}\n  - {id: a, agent: default}\n",
    )
    h = Harness(roots(("runtime", tmp_path), ("bundled", REPO)))
    assert "unique" in str(h.errors[0])


def test_commands_kind_loads_overrides_and_validates(tmp_path: Path) -> None:
    overlay, bundled = tmp_path / "overlay", tmp_path / "bundled"
    write(bundled / "commands" / "review.md", "---\nname: review\ndescription: base\n---\nReview {{args}}\n")
    write(bundled / "commands" / "README.md", "# not a command\n")
    write(overlay / "commands" / "review.md", "---\nname: review\ndescription: mine\n---\nMine: {{args}}\n")
    write(overlay / "commands" / "bad.md", "---\nname: bad\ndescription: x\n---\n{{nope}}\n")
    write(overlay / "commands" / "odd.md", "---\nname: odd\ndescription: x\nhandler: launch\n---\n")
    h = Harness(roots(("runtime", overlay), ("bundled", bundled)))
    assert set(h.commands) == {"review"}
    assert h.commands["review"].manifest.description == "mine"
    assert h.commands["review"].template == "Mine: {{args}}"
    assert h.commands["review"].manifest.handler == "prompt"
    messages = " ".join(str(e) for e in h.errors)
    assert "unknown template variable" in messages and "handler" in messages
    assert [r["name"] for r in h.describe()["commands"]] == ["review"]


def test_bundled_commands_are_valid(tmp_path: Path) -> None:
    h = Harness(default_roots(tmp_path / "h", tmp_path, REPO))
    handlers = {n: c.manifest.handler for n, c in h.commands.items()}
    assert handlers == {
        "babysit_pr": "prompt",  # G6
        "compact": "compact",
        "curator": "prompt",
        "goal": "prompt",
        "model": "model",
        "plan": "plan",
        "review": "prompt",
    }
