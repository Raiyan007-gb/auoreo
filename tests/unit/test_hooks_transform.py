"""H2: the `transform` hook event folds a payload through matching hook commands (spec §2.11)."""

from __future__ import annotations

from pathlib import Path

from auoreo.core.services import Services
from auoreo.runtime.hooks import HookRunner, hooks_of
from tests.unit.test_hooks import hook, reply


def runner(services: Services) -> HookRunner:
    h = hooks_of(services)
    assert h is not None
    return h


async def test_transform_chains_in_name_order(services: Services, workdir: Path) -> None:
    add = "p = payload['payload']; p['n'] = p.get('n', 0) + 1\nprint(json.dumps({'payload': p}))\n"
    double = (
        "p = payload['payload']; p['n'] *= 10; p['kind'] = payload['kind']\n"
        "print(json.dumps({'payload': p}))\n"
    )
    hook(workdir, "a_add", "transform", add, matcher="webhook:*")
    hook(workdir, "b_double", "transform", double, matcher="webhook:*")
    hook(workdir, "c_other", "transform", reply({"payload": {"hijacked": True}}), matcher="delivery:*")
    services.harness.reload()
    out = await runner(services).transform("webhook:gh", {"n": 1, "keep": "yes"})
    assert out == {"n": 20, "keep": "yes", "kind": "webhook:gh"}
    # no matching hook: unchanged; an empty reply keeps the payload
    assert await runner(services).transform("cron:x", {"a": 1}) == {"a": 1}


async def test_transform_drop_and_error_policy(services: Services, workdir: Path) -> None:
    hook(workdir, "dropper", "transform", reply({"drop": True, "reason": "spam"}), matcher="webhook:spam")
    hook(workdir, "broken", "transform", "sys.exit(3)\n", matcher="webhook:broken", on_error="block")
    hook(workdir, "flaky", "transform", "sys.exit(3)\n", matcher="webhook:flaky", on_error="warn")
    hook(workdir, "noop", "transform", "print('{}')\n", matcher="webhook:noop")
    services.harness.reload()
    r = runner(services)
    assert await r.transform("webhook:spam", {"x": 1}) is None
    assert await r.transform("webhook:broken", {"x": 1}) is None  # on_error: block drops
    assert await r.transform("webhook:flaky", {"x": 1}) == {"x": 1}  # on_error: warn keeps
    assert await r.transform("webhook:noop", {"x": 2}) == {"x": 2}
    ran = [e.data for e in await services.events.replay(0, frozenset({"server"})) if e.type == "hook.ran"]
    assert {(d["name"], d["decision"]) for d in ran} >= {("dropper", "deny"), ("noop", "allow")}
