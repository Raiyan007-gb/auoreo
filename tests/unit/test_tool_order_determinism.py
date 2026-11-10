"""M7 step 1.8: the tool array of a request is byte-stable.

A provider prompt cache keys on the exact bytes of the tool block, so the order must not
depend on the order in which MCP servers or plugins happened to register their tools, nor on
when a deferred tool was loaded, nor on the process (no hash-order dependence).
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

from auoreo.gateway.shaper import shape_for_model
from auoreo.gateway.types import tool_schema
from auoreo.harness.discovery import default_roots
from auoreo.harness.loader import ToolDef
from auoreo.harness.manifests import AgentManifest, ToolManifest
from auoreo.harness.orchestrator import build_spec
from auoreo.harness.registry import Harness
from auoreo.runtime.runner import AgentRunner
from auoreo.tools.context import SessionState
from tests.conftest import REPO

DYNAMIC = ("mcp__zeta__list", "mcp__alpha__write", "plugin__beta__run", "mcp__alpha__read")


def _harness(home: Path, workdir: Path, order: tuple[str, ...]) -> Harness:
    h = Harness(default_roots(home, workdir, REPO, []))
    for name in order:
        params = {"type": "object", "properties": {"b": {"type": "string"}, "a": {"type": "integer"}}}
        manifest = ToolManifest(name=name, description=f"dynamic {name}", parameters=params)
        h.register_dynamic(ToolDef(manifest, name.split("__")[1], None))
    return h


def _names(h: Harness) -> list[str]:
    return [t.name for t in h.select_tools(AgentManifest(name="a", description="d"))]


def _wire(tools: list[ToolDef], model: str) -> str:
    schemas = [tool_schema(t.name, t.manifest.description, t.manifest.parameters) for t in tools]
    return json.dumps(shape_for_model(model, schemas))


def test_registration_order_does_not_change_the_selected_order(home: Path, workdir: Path) -> None:
    orders = list(itertools.permutations(DYNAMIC))
    first = _names(_harness(home, workdir, orders[0]))
    assert set(DYNAMIC) <= set(first)
    for order in orders[1:]:
        assert _names(_harness(home, workdir, order)) == first


def test_builtins_come_first_and_each_group_is_sorted(home: Path, workdir: Path) -> None:
    names = _names(_harness(home, workdir, DYNAMIC))
    builtins = [n for n in names if n not in DYNAMIC]
    assert names == [*sorted(builtins), *sorted(DYNAMIC)]


def test_reregistering_a_tool_keeps_its_place(home: Path, workdir: Path) -> None:
    h = _harness(home, workdir, DYNAMIC)
    before = _names(h)
    h.unregister_dynamic("mcp__alpha__")  # a server reconnect: drop and re-add its tools
    for name in ("mcp__alpha__write", "mcp__alpha__read"):
        h.register_dynamic(ToolDef(ToolManifest(name=name, description=f"dynamic {name}"), "alpha", None))
    assert _names(h) == before


def test_wire_tool_array_is_byte_identical_across_harnesses(home: Path, workdir: Path) -> None:
    a = _harness(home, workdir, DYNAMIC)
    b = _harness(home, workdir, tuple(reversed(DYNAMIC)))
    agent = AgentManifest(name="a", description="d")
    for model in ("anthropic/claude-sonnet-4-5", "openai/gpt-5", "gemini/gemini-2.5-pro"):
        assert _wire(a.select_tools(agent), model) == _wire(b.select_tools(agent), model)


def test_loaded_deferred_tools_do_not_depend_on_load_order(home: Path, workdir: Path) -> None:
    """`tool_search` adds names to a set; the request order must come from the spec, not the set."""
    h = _harness(home, workdir, DYNAMIC)
    spec = build_spec(h, "default", "openai/gpt-5", "manual")
    deferred = [t.name for t in spec.deferred]
    assert len(deferred) >= 2, "the bundled catalog has deferred tools"
    runner = AgentRunner.__new__(AgentRunner)  # `_available` reads only its arguments
    one, two = SessionState(), SessionState()
    for name in deferred:
        one.loaded_tools.add(name)
    for name in reversed(deferred):
        two.loaded_tools.add(name)
    first = [t.name for t in runner._available(spec, one)]
    assert first == [t.name for t in runner._available(spec, two)]
    assert first == [*[t.name for t in spec.tools], *deferred]
