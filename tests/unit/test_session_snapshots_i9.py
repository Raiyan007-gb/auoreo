"""I9 (spec §20.4, §24.10 L880): session-replay snapshots. A fake-gateway session runs a fixed
script (text turn, tool turn); its event stream (types + payloads, with ids, timestamps,
durations and generated ids normalised away) must equal the committed snapshot in
`tests/fixtures/snapshots/<case>.json`.

Refresh locally after an intended change (never in CI):
    AUOREO_SNAPSHOT=update uv run pytest tests/unit/test_session_snapshots_i9.py
VCR cassettes cover gateway HTTP; these snapshots cover the session replay (the split of §24.10).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from auoreo.core.services import Services
from auoreo.gateway.fake import FakeGateway, Scripted

# Volatile fields: generated ids, timestamps, durations, token counts (they move with every
# prompt/tool-catalog edit), the host tmp path; what remains is the event *shape* of a session.

SNAPSHOTS = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots"
VOLATILE_KEYS = re.compile(
    r"(^|_)(id|ids|at|ts|time|ms|duration|elapsed|latency|seq|created|updated|started|finished)$"
    r"|tokens$|^tools$|^cwd$|^cost_usd$|^generation$"  # prompt size / catalog / host-dependent
    r"|^cache_hit_ratio$"  # a ratio of prompt sizes
)
GENERATED = re.compile(
    r"\b(ses|run|msg|evt|call|tc|req)_[A-Za-z0-9]{6,}\b|\b[0-9a-f]{32}\b|\b[0-9a-f-]{36}\b"
)
SKIP_TYPES = frozenset({"session.title", "usage.recorded", "tool.progress"})


def normalise(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: normalise(v, k) for k, v in sorted(value.items()) if not VOLATILE_KEYS.search(k)}
    if isinstance(value, list):
        return [normalise(v, key) for v in value]
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, str):
        return GENERATED.sub("<id>", value)
    return value


async def session_stream(
    services: Services, gateway: FakeGateway, prompt: str, script: list[Scripted]
) -> list[Any]:
    gateway.queue(*script)
    sess = await services.create_session()
    await services.coordinator.submit(sess.id, prompt, interactive=False)
    await services.coordinator.wait(sess.id, 20)
    events = await services.events.replay(0, frozenset({f"session:{sess.id}"}))
    return [
        {"type": e.type, "data": normalise(e.data)}
        for e in events
        if e.type not in SKIP_TYPES and not e.type.startswith("title")
    ]


def check(case: str, stream: list[Any]) -> None:
    path = SNAPSHOTS / f"{case}.json"
    text = json.dumps(stream, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if os.environ.get("AUOREO_SNAPSHOT") == "update" or not path.exists():
        if os.environ.get("CI") and not path.exists():
            pytest.fail(f"snapshot {path.name} missing; record it locally with AUOREO_SNAPSHOT=update")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(text), (
        f"session replay drifted from {path}; if intended: AUOREO_SNAPSHOT=update"
    )


async def test_text_turn_snapshot(services: Services, gateway: FakeGateway) -> None:
    stream = await session_stream(services, gateway, "hello there", [Scripted(text="General Kenobi.")])
    assert any(e["type"].startswith("message") for e in stream)
    check("text_turn", stream)


async def test_tool_turn_snapshot(services: Services, gateway: FakeGateway, workdir: Path) -> None:
    (workdir / "notes.txt").write_text("alpha\nbeta\n")
    script = [
        Scripted(tool_calls=[("read", {"path": "notes.txt"})]),
        Scripted(text="It says alpha and beta."),
    ]
    stream = await session_stream(services, gateway, "what is in notes.txt?", script)
    assert any("tool" in e["type"] for e in stream)
    check("tool_turn", stream)


def test_normalise_strips_volatile_fields() -> None:
    raw = {
        "id": "x",
        "created_at": "t",
        "text": "run_abcdef123 done",
        "n": 1.23456,
        "nested": [{"duration_ms": 4}],
    }
    assert normalise(raw) == {"nested": [{}], "n": 1.235, "text": "<id> done"}
