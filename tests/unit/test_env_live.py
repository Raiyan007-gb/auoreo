"""ENV: live apply (in place, watcher, harness reload) and custom variables (shell injection +
masking, the agent's per-turn view, `{{var.NAME}}` placeholders, name rules)."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from sqlalchemy import select

from auoreo.api.app import create_app
from auoreo.core.db import AuditRow
from auoreo.core.env_custom import name_problem, resolve_vars, shell_env
from auoreo.core.live_settings import apply, live_of
from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.core.settings import Settings
from auoreo.gateway.fake import FakeGateway, Scripted
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.tools.context import ToolContext
from tests.unit.test_loop_recovery import run, turns

SECRET = "zq-custom-secret-value-0123456789"


@pytest.fixture
async def client(services: Services) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(services.settings, services=services)
    async with (
        LifespanManager(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as c,
    ):
        yield c


async def _custom(client: httpx.AsyncClient, name: str, value: str, **flags: bool) -> None:
    r = await client.post("/api/env/custom", json={"name": name, "value": value, **flags})
    assert r.status_code == 201, r.text


# ------------------------------------------------------------------ apply + watcher
async def test_apply_updates_in_place_and_skips_restart_keys(services: Services, home: Path) -> None:
    held = services.settings
    new = Settings(home=home, gateway="fake", approval_timeout_s=5, locale="ja-JP", port=9999)
    res = await apply(services, new)
    assert res.live == ["locale"] and res.restart_required == ["port"]
    assert held is services.settings and held.locale == "ja-JP" and held.port == 4096


async def test_watcher_picks_up_external_settings_edit(services: Services) -> None:
    live = live_of(services)
    path = services.settings.home / "settings.json"
    path.write_text(json.dumps({"suggestions_max_pending": 9, "strict_overlays": True}))
    os.utime(path, ns=(1, 2_000_000_000_000_000_000))
    gen = services.harness.generation
    await live.poll_once()
    assert services.settings.suggestions_max_pending == 9 and services.settings.strict_overlays
    assert services.harness.strict_overlays and services.harness.generation == gen + 1
    path.write_text("{ broken")
    os.utime(path, ns=(1, 3_000_000_000_000_000_000))
    await live.poll_once()  # a broken file is logged and ignored: running settings stay
    assert services.settings.suggestions_max_pending == 9
    services.secrets.set("BRAVE_API_KEY", "brave-key-0123456789")
    await live.poll_once()
    events = [e for e in await services.events.replay(0) if e.type in {"settings.changed", "secret.changed"}]
    assert events[0].data["source"] == "watch" and events[-1].data == {"name": None, "action": "external"}


async def test_init_overrides_survive_a_rebuild(services: Services) -> None:
    (services.settings.home / "settings.json").write_text(json.dumps({"gateway": "litellm"}))
    res = await live_of(services).reload()
    assert services.settings.gateway == "fake" and "gateway" not in res.changed


# ------------------------------------------------------------------ custom variables
def test_name_rules() -> None:
    assert name_problem("MY_VARIABLE") is None
    for bad in (
        "my_var",
        "A",
        "AUOREO_X",
        "PATH",
        "LD_PRELOAD",
        "PYTHONPATH",
        "HTTPS_PROXY",
        "OPENAI_API_KEY",
    ):
        assert name_problem(bad) is not None, bad
    assert name_problem("NODE_OPTIONS") and name_problem("GIT_DIR") and name_problem("SERVICE_URL")


async def test_custom_vars_crud_names_only(services: Services, client: httpx.AsyncClient) -> None:
    await _custom(client, "MY_PLAIN", "hello", expose_to_agent=True)
    await _custom(client, "MY_TOKEN_X", SECRET, secret=True, expose_to_shell=True)
    data = json.loads((services.settings.home / "settings.json").read_text())
    assert data["env"]["custom"]["MY_PLAIN"]["value"] == "hello"
    assert "value" not in data["env"]["custom"]["MY_TOKEN_X"] and SECRET not in json.dumps(data)
    assert services.secrets.get("MY_TOKEN_X") == SECRET
    listing = await client.get("/api/env")
    assert SECRET not in listing.text
    rows = {c["name"]: c for c in listing.json()["custom"]}
    assert rows["MY_TOKEN_X"]["is_set"] and rows["MY_TOKEN_X"]["value"] is None
    assert (await client.post("/api/env/custom", json={"name": "MY_PLAIN", "value": "x"})).status_code == 409
    assert (await client.post("/api/env/custom", json={"name": "PATH", "value": "x"})).status_code == 422
    r = await client.put("/api/env/custom/MY_TOKEN_X", json={"name": "MY_TOKEN_X", "secret": False})
    assert r.status_code == 422  # secret → plain needs a fresh value (the secret is never shown)
    r = await client.put("/api/env/custom/MY_PLAIN", json={"name": "MY_PLAIN", "value": "bye"})
    assert r.status_code == 200 and services.settings.env.custom["MY_PLAIN"].value == "bye"
    assert (await client.delete("/api/env/custom/MY_TOKEN_X")).status_code == 200
    assert services.secrets.get("MY_TOKEN_X") is None and "MY_TOKEN_X" not in services.settings.env.custom


async def test_secret_var_injected_only_into_flagged_shell_tools_and_masked(
    services: Services, client: httpx.AsyncClient, workdir: Path
) -> None:
    await _custom(client, "MY_TOKEN_X", SECRET, secret=True, expose_to_shell=True)
    await _custom(client, "MY_HIDDEN", "not-exported", expose_to_shell=False)
    assert shell_env(services, "glob") == {}
    assert set(shell_env(services, "bash")) == {"MY_TOKEN_X"}
    ctx = ToolContext(session_id="s1", cwd=workdir, home=services.settings.home, harness=services.harness)
    out = await services.coordinator.runner.executor.run(
        ToolCall("c1", "bash", json.dumps({"command": 'echo "v=$MY_TOKEN_X h=${MY_HIDDEN:-unset}"'})),
        tools=services.harness.tools,
        ctx=ctx,
        engine=PermissionEngine([Rule(permission="*", pattern="*", action="allow")]),
    )
    content = out.result.content
    assert "v=***" in content and SECRET not in content and "h=unset" in content
    async with services.db.session() as s:
        rows = [r for r in (await s.execute(select(AuditRow))).scalars() if r.action == "secret.read"]
    assert any(services.audit.plain_detail(r) == {"name": "MY_TOKEN_X", "by": "bash"} for r in rows)


async def test_expose_to_agent_lists_names_in_next_turn_context(
    services: Services, client: httpx.AsyncClient, gateway: FakeGateway
) -> None:
    gateway.queue(Scripted(text="one"), Scripted(text="two"))
    await run(services, "hi")
    assert "MY_PLAIN" not in json.dumps(turns(gateway)[-1].messages)
    await _custom(client, "MY_PLAIN", "plain-value", expose_to_agent=True)
    await _custom(client, "MY_TOKEN_X", SECRET, secret=True, expose_to_agent=True, expose_to_shell=True)
    await _custom(client, "MY_QUIET", "quiet-value")
    status, _, _ = await run(services, "again")
    assert status == "completed"
    sent = json.dumps(turns(gateway)[-1].messages)
    assert "MY_PLAIN = plain-value" in sent and "MY_TOKEN_X (secret; value hidden" in sent
    assert SECRET not in sent and "MY_QUIET" not in sent
    assert "MY_PLAIN" not in turns(gateway)[-1].messages[0]["content"]  # never the system prompt


def test_placeholders_resolve_plain_vars_only(settings: Settings) -> None:
    settings.env.custom = {
        "MY_PLAIN": settings.env.custom.get("X") or _var(value="abc"),
        "MY_SECRET": _var(secret=True),
    }
    text = "a={{var.MY_PLAIN}} b={{ var.MY_SECRET }} c={{var.MISSING}} d={{args}}"
    assert resolve_vars(text, settings) == "a=abc b= c= d={{args}}"


async def test_model_change_rebuilds_the_litellm_router(services: Services) -> None:
    from auoreo.gateway.gateway import LiteLLMGateway

    gw = LiteLLMGateway(services.settings, lambda n: "sk-env" if n.endswith("_API_KEY") else None)
    services.gateway = gw
    before = gw._router
    (services.settings.home / "settings.json").write_text(json.dumps({"model": "openai/gpt-5"}))
    res = await live_of(services).reload("api")
    assert "models" in res.live and gw._router is not before
    names = [e["litellm_params"]["model"] for e in gw._model_list if e["model_name"] == "default"]
    assert names == ["openai/gpt-5"]


def _var(**kw: Any) -> Any:
    from auoreo.core.settings_env import CustomVar

    return CustomVar(**kw)
