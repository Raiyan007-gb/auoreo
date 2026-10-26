"""Spec §2.11/§15: plugin + MCP skills merged into the agent skill catalog, `@skill` mentions,
per-session source toggles."""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server import MCPServer

from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.harness.manifests import McpServerManifest
from auoreo.harness.orchestrator import build_spec
from auoreo.harness.skill_sources import SkillCatalog, make_skill, mentions, rank
from auoreo.mcp.bridge import McpBridge
from auoreo.runtime.session_sources import disabled_sources, set_disabled, without_sources


def skill_server() -> MCPServer:
    srv = MCPServer("docs")

    @srv.resource("skill://style-guide", name="style-guide", description="House writing style")
    def style() -> str:
        return "# Style\nUse short sentences."

    @srv.resource("docs://skills/code-review", name="code-review", description="Shadowed by the folder skill")
    def review() -> str:
        return "MCP code review"

    @srv.resource("docs://readme", name="readme")
    def readme() -> str:
        return "not a skill"

    @srv.tool()
    def ping() -> str:
        """Ping."""
        return "pong"

    return srv


async def _connect(services: Services) -> McpBridge:
    bridge = McpBridge(services)
    services.mcp = bridge
    await bridge.connect(McpServerManifest(name="docs", command="x", exposure="direct"), skill_server())
    return bridge


def test_rank_order_runtime_project_plugin_bundled_mcp() -> None:
    """Spec §24.10 L890: runtime > project > plugin > bundled > (extra roots) > MCP."""
    assert (
        rank("runtime")
        < rank("project")
        < rank("plugin:x")
        < rank("bundled")
        < rank("extra:0")
        < rank("mcp:y")
    )


def test_plugin_skill_beats_bundled_but_not_project() -> None:
    from types import SimpleNamespace

    cat = SkillCatalog()
    cat.set("plugin:p", [make_skill("plugin:p", "shared", "from plugin", text="p")])
    bundled = SimpleNamespace(name="shared", source="bundled")
    merged, collisions = cat.merged({"shared": bundled})
    assert merged["shared"].source == "plugin:p"
    assert collisions[0]["winner"] == "plugin:p" and collisions[0]["loser"] == "bundled"
    project = SimpleNamespace(name="shared", source="project")
    merged, _ = cat.merged({"shared": project})
    assert merged["shared"] is project


def test_catalog_precedence_and_collisions() -> None:
    cat = SkillCatalog()
    cat.set("mcp:a", [make_skill("mcp:a", "shared", "from mcp", text="m")])
    cat.set("plugin:p", [make_skill("plugin:p", "shared", "from plugin", text="p")])
    merged, collisions = cat.merged({})
    assert merged["shared"].source == "plugin:p"  # plugin beats MCP even when registered later
    assert collisions == [
        {"name": "shared", "winner": "plugin:p", "loser": "mcp:a", "qualified": "mcp:a:shared"}
    ]
    assert cat.get("mcp:a:shared") is not None
    assert cat.drop("plugin:p") and cat.merged({})[0]["shared"].source == "mcp:a"


def test_make_skill_validates_names() -> None:
    assert make_skill("plugin:p", "ok-name", "").manifest.description == "ok-name"
    try:
        make_skill("plugin:p", "Bad Name", "x")
    except ValueError as exc:
        assert "kebab" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid name accepted")


def test_mentions_parse_names_and_skip_emails() -> None:
    text = "use @code-review and @plugin:echo:echo-usage, not me@example.com; again @code-review."
    assert mentions(text) == ["code-review", "plugin:echo:echo-usage"]


async def test_mcp_skill_resources_join_the_catalog(services: Services) -> None:
    bridge = await _connect(services)
    h = services.harness
    assert h.all_skills["style-guide"].source == "mcp:docs"
    assert h.all_skills["code-review"].source == "bundled"  # folder skill wins the clash
    assert {c["qualified"] for c in h.skill_collisions} == {"mcp:docs:code-review"}
    spec = build_spec(h, "default", "default", "manual")
    assert "style-guide" in {s.name for s in spec.skills}
    assert "`style-guide` — House writing style" in spec.system_prompt
    shadowed = h.resolve_skill("mcp:docs:code-review")
    assert shadowed is not None and await shadowed.load() == "MCP code review"
    rows = {r["name"]: r for r in h.describe()["skills"]}
    assert rows["style-guide"]["source"] == "mcp:docs" and rows["style-guide"]["path"] is None
    await bridge.stop()
    assert "style-guide" not in h.all_skills


async def test_skill_tool_loads_mcp_and_qualified_skills(services: Services, gateway: FakeGateway) -> None:
    bridge = await _connect(services)
    gateway.queue(
        Scripted(tool_calls=[("skill", {"name": "style-guide"})]),
        Scripted(tool_calls=[("skill", {"name": "mcp:docs:code-review"})]),
        Scripted(text="done"),
    )
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "write it", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    second = gateway.requests[1].messages
    assert second[-1]["content"].startswith('<skill name="style-guide">')
    assert "Use short sentences." in second[-1]["content"]
    third = gateway.requests[2].messages
    assert "MCP code review" in third[-1]["content"]
    await bridge.stop()


async def test_at_mention_loads_the_skill_for_the_turn(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "please @code-review my diff", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    sent = "\n".join(str(m["content"]) for m in gateway.requests[0].messages if m["role"] == "user")
    assert sent.index("please @code-review my diff") < sent.index('<skill name="code-review">')
    hist = await services.coordinator.runner.load_history(sess.id)
    assert hist[1]["role"] == "user" and hist[1]["content"].startswith('<skill name="code-review">')
    events = await services.events.replay(0, frozenset({f"session:{sess.id}"}))
    loaded = [e.data for e in events if e.type == "skill.loaded"]
    assert loaded and loaded[0]["name"] == "code-review" and loaded[0]["via"] == "mention"


async def test_unknown_mention_is_ignored(services: Services, gateway: FakeGateway) -> None:
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, "ping @nobody-here", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    assert gateway.requests[0].messages[-1]["content"].endswith("ping @nobody-here")


async def test_session_sources_hide_mcp_tools_and_skills(services: Services, gateway: FakeGateway) -> None:
    bridge = await _connect(services)
    sess = await services.create_session()
    assert await disabled_sources(services, sess.id) == []
    assert await set_disabled(services, sess.id, ["mcp:docs"]) == ["mcp:docs"]
    gateway.queue(Scripted(tool_calls=[("skill", {"name": "style-guide"})]), Scripted(text="ok"))
    await services.coordinator.submit(sess.id, "go", interactive=False)
    await services.coordinator.wait(sess.id, 10)
    tools = {t["function"]["name"] for t in gateway.requests[0].tools}
    assert "mcp__docs__ping" not in tools
    hist = await services.coordinator.runner.load_history(sess.id)
    tool_msg = next(m for m in hist if m["role"] == "tool")
    assert "unknown skill 'style-guide'" in tool_msg["content"]
    # Another session is unaffected.
    other = await services.create_session()
    gateway.queue(Scripted(text="hi"))
    await services.coordinator.submit(other.id, "go", interactive=False)
    await services.coordinator.wait(other.id, 10)
    assert "mcp__docs__ping" in {t["function"]["name"] for t in gateway.requests[-1].tools}
    await bridge.stop()


def test_without_sources_filters_spec(services: Services) -> None:
    spec = build_spec(services.harness, "default", "default", "manual")
    plain = without_sources(spec, [])
    assert plain is spec
    off = without_sources(spec, ["mcp:none"])
    assert off.extras["disabled_sources"] == ("mcp:none",)


async def test_set_disabled_rejects_bad_ids(services: Services) -> None:
    sess = await services.create_session()
    try:
        await set_disabled(services, sess.id, ["tools:bash"])
    except ValueError as exc:
        assert "mcp:<server>" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("bad id accepted")


async def test_plugin_skill_defs_register_and_unregister(services: Services, workdir: Path) -> None:
    from auoreo.plugins.bridge import PluginSkill, plugin_skill_defs

    defs = plugin_skill_defs(
        "demo",
        [PluginSkill("demo", "demo-usage", "How to demo", "Call demo."), PluginSkill("demo", "Bad", "", "")],
    )
    assert [d.name for d in defs] == ["demo-usage"]
    services.harness.register_skills("plugin:demo", defs)
    assert services.harness.all_skills["demo-usage"].source == "plugin:demo"
    gen = services.harness.generation
    services.harness.unregister_skills("plugin:demo")
    assert "demo-usage" not in services.harness.all_skills and services.harness.generation == gen + 1
    assert json.dumps([d.qualified for d in defs]) == '["plugin:demo:demo-usage"]'
