"""G4a: the `SubagentStop` hook event fires on the parent when a delegated child run ends."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from auoreo.core.services import Services
from auoreo.gateway.fake import Scripted
from auoreo.harness.manifests import HookManifest
from tests.conftest import write
from tests.teams_helpers import RoutedGateway, new_parent, turn


@pytest.fixture
def gateway() -> RoutedGateway:
    return RoutedGateway()


def test_subagent_stop_is_a_hook_event() -> None:
    m = HookManifest(name="h", description="d", event="SubagentStop", command=["true"])
    assert m.event == "SubagentStop"


async def test_subagent_stop_fires_with_child_outcome(
    services: Services, gateway: RoutedGateway, workdir: Path
) -> None:
    out = workdir / "stops.jsonl"
    script = (
        "import json, sys, os\npayload = json.load(sys.stdin)\n"
        "row = {'event': os.environ['AUOREO_HOOK_EVENT'], **payload}\n"
        f"open({str(out)!r}, 'a').write(json.dumps(row) + '\\n')\n"
    )
    write(workdir / ".auoreo" / "scripts" / "stop.py", script)
    write(
        workdir / ".auoreo" / "hooks" / "on_child_stop.yaml",
        "name: on_child_stop\ndescription: record child stops\nevent: SubagentStop\nmatcher: planner\n"
        f"command: ['{sys.executable}', './scripts/stop.py']\n",
    )
    services.harness.reload()
    args = {"goal": "CHILD-SS do it", "agent": "planner", "name": "kid"}
    gateway.route("PARENT-SS", Scripted(tool_calls=[("delegate_task", args)]), Scripted(text="ok"))
    gateway.route("CHILD-SS", Scripted(text="child finished"))
    parent = await new_parent(services)
    result = await turn(services, parent, "PARENT-SS go")
    assert result.status == "completed"
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "SubagentStop" and row["parent"] == parent and row["agent"] == "planner"
    assert row["name"] == "kid" and row["status"] == "completed" and row["child"]
