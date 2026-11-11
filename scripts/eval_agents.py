"""Agent evals: run the cases in `agents/<name>/evals/*.yaml` (M7 W3, `auoreo eval agents`).

On the fake gateway a case checks an agent profile's contract — its tool list, permission
outcomes and unattended denials — not model quality: the model's turns are the case's
`script`. Every case runs in a fresh temporary home and workspace, non-interactively (a call
that would ask for approval is refused). `--gateway live` runs the same cases against the
configured model and ignores `script`; it needs real keys and is never run by CI.

A case file:

    description: what the case checks
    input: "the user message"
    unattended: webhook            # optional: job | webhook (headless delivery session)
    script:                        # fake-gateway turns, in order
      - tool_calls: [{name: read, args: {path: README.md}}]
      - text: "done"
    expect:
      tools_available: [read, ...] # exactly these tools (direct + deferred)
      tools_called: [read]         # each was called at least once
      tools_denied: [bash]         # every call to each ended in a tool error
      final_matches: "^done"       # re.search on the final answer
      events: [tool.completed]     # event types the session emitted
      max_steps: 2                 # model calls at most

Exit 0 when every case passes, 1 when one fails, 2 for an unknown agent or a malformed case.
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

TIMEOUT_S = 60.0


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Call(_Strict):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)


class Step(_Strict):
    text: str = ""
    tool_calls: list[Call] = Field(default_factory=list)


class Expect(_Strict):
    tools_available: list[str] | None = None
    tools_called: list[str] = Field(default_factory=list)
    tools_denied: list[str] = Field(default_factory=list)
    final_matches: str | None = None
    events: list[str] = Field(default_factory=list)
    max_steps: int | None = Field(default=None, ge=1)


class Case(_Strict):
    description: str = ""
    input: str = Field(min_length=1)
    unattended: Literal["job", "webhook"] | None = None
    script: list[Step] = Field(default_factory=list)
    expect: Expect = Field(default_factory=Expect)


@dataclass
class Result:
    agent: str
    case: str
    ok: bool
    failures: list[str] = field(default_factory=list)
    steps: int = 0
    called: list[str] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)


class Malformed(Exception):
    """An unknown agent or a case file that does not parse."""


def load_case(path: Path) -> Case:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return Case.model_validate(data if isinstance(data, dict) else {})
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise Malformed(f"{path}: {exc}") from exc


def settings_for(home: Path, roots: list[Path], live: bool) -> Any:
    from auoreo.core.settings import Settings

    gateway = "litellm" if live else "fake"
    return Settings(home=home, gateway=gateway, extra_roots=roots, approval_timeout_s=1)


async def discover(names: list[str], roots: list[Path]) -> list[tuple[str, str, Case]]:
    """(agent, case name, case) for the named agents (all agents with cases when none)."""
    from auoreo.core.paths import bundled_root
    from auoreo.harness.discovery import default_roots
    from auoreo.harness.registry import Harness

    with tempfile.TemporaryDirectory(prefix="auoreo-eval-agents-") as tmp:
        harness = Harness(default_roots(Path(tmp) / "home", Path(tmp), bundled_root(), roots))
    agents = dict(harness.agents)
    unknown = [n for n in names if n not in agents]
    if unknown:
        raise Malformed(f"unknown agent(s): {', '.join(unknown)} (known: {', '.join(sorted(agents))})")
    out: list[tuple[str, str, Case]] = []
    for name in names or sorted(agents):
        folder = agents[name].path.parent / "evals"
        for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
            out.append((name, path.stem, load_case(path)))
    return out


def scripted(case: Case) -> Any:
    from auoreo.gateway.fake import FakeGateway, Scripted

    steps = [
        Scripted(text=s.text, tool_calls=[(c.name, dict(c.args)) for c in s.tool_calls]) for s in case.script
    ]
    return FakeGateway(steps)


def available(svc: Any, agent: str, case: Case) -> set[str]:
    from auoreo.automation.deliveries import UNATTENDED_DENY
    from auoreo.harness.orchestrator import build_spec

    spec = build_spec(svc.harness, agent, "default", "manual")
    names = {t.name for t in spec.tools} | {t.name for t in spec.deferred}
    if case.unattended:
        names = {n for n in names if not any(fnmatch.fnmatchcase(n, g) for g in UNATTENDED_DENY)}
    return names


async def run_case(agent: str, name: str, case: Case, roots: list[Path], live: bool) -> Result:
    from auoreo.automation.deliveries import UNATTENDED_DENY
    from auoreo.core.db import DeliveryRow
    from auoreo.core.services import Services
    from auoreo.runtime.inbox import RunOptions

    with tempfile.TemporaryDirectory(prefix="auoreo-eval-agents-") as tmp:
        home, work = Path(tmp) / "home", Path(tmp) / "work"
        home.mkdir()
        work.mkdir()
        (work / "README.md").write_text("# eval workspace\n", encoding="utf-8")
        settings = settings_for(home, roots, live)
        gateway = None if live else scripted(case)
        svc = await Services.create(settings, cwd=work, gateway=gateway, with_mcp=False, with_memory=False)
        try:
            ses = await svc.create_session(title=f"eval: {agent}/{name}", agent=agent)
            options = None
            if case.unattended:
                async with svc.db.session() as s:
                    s.add(
                        DeliveryRow(
                            kind=case.unattended, source=f"eval-{name}", external_id=f"eval-{ses.id}",
                            payload={}, result={}, status="delivering", session_id=ses.id,
                        )
                    )  # fmt: skip
                    await s.commit()
                options = RunOptions(tools_deny=UNATTENDED_DENY, skip_memory=True)
            run_id = await svc.coordinator.submit(ses.id, case.input, interactive=False, options=options)
            await svc.coordinator.wait(ses.id, TIMEOUT_S)
            final = svc.coordinator.result(run_id or "")
            events = await svc.events.replay(0, frozenset({f"session:{ses.id}"}))
            tools = available(svc, agent, case)
            steps = len([e for e in events if e.type == "step.started"])
        finally:
            await svc.close()
    done = [e.data for e in events if e.type == "tool.completed"]
    called = sorted({d["tool"] for d in done})
    denied = sorted(
        {d["tool"] for d in done if d.get("is_error")} - {d["tool"] for d in done if not d.get("is_error")}
    )
    res = Result(agent, name, True, steps=steps, called=called, denied=denied)
    res.failures = check(
        case.expect, tools, called, denied, final.text if final else "", {e.type for e in events}, steps, live
    )
    res.ok = not res.failures
    return res


def check(
    ex: Expect,
    tools: set[str],
    called: list[str],
    denied: list[str],
    text: str,
    types: set[str],
    steps: int,
    live: bool,
) -> list[str]:
    out: list[str] = []
    if ex.tools_available is not None:
        want = set(ex.tools_available)
        out += [f"unexpected tool {t}" for t in sorted(tools - want)]
        out += [f"missing tool {t}" for t in sorted(want - tools)]
    out += [f"{t} was never called" for t in ex.tools_called if t not in called]
    out += [f"{t} was not denied" for t in ex.tools_denied if t not in denied]
    if ex.final_matches is not None and not re.search(ex.final_matches, text):
        out.append(f"final answer {text[:80]!r} does not match {ex.final_matches!r}")
    out += [f"no {t} event" for t in ex.events if t not in types]
    if ex.max_steps is not None and not live and steps > ex.max_steps:
        out.append(f"{steps} model calls (max {ex.max_steps})")
    return out


async def run_all(names: list[str], roots: list[Path], live: bool) -> list[Result]:
    cases = await discover(names, roots)
    if not cases:
        raise Malformed(f"no eval cases (agents/<name>/evals/*.yaml) for {', '.join(names) or 'any agent'}")
    return [await run_case(agent, name, case, roots, live) for agent, name, case in cases]


def print_table(results: list[Result]) -> None:
    print(f"{'agent':<16} {'case':<24} {'result':<6} detail")
    for r in results:
        detail = (
            "; ".join(r.failures) if r.failures else f"{r.steps} step(s), called {', '.join(r.called) or '-'}"
        )
        print(f"{r.agent:<16} {r.case:<24} {'PASS' if r.ok else 'FAIL':<6} {detail}")
    print(f"{sum(r.ok for r in results)}/{len(results)} case(s) passed")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("agents", nargs="*", help="agent names (default: every agent with an evals/ folder)")
    ap.add_argument(
        "--root", action="append", type=Path, default=[], help="extra definition root (repeatable)"
    )
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--gateway", choices=("fake", "live"), default="fake", help="live: real model, never in CI"
    )
    args = ap.parse_args(argv)
    try:
        results = asyncio.run(run_all(list(args.agents), list(args.root), args.gateway == "live"))
    except Malformed as exc:
        print(f"eval agents: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print_table(results)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
