"""Shared fixtures for the W2-C perception tests (generated images, tool calls)."""

from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, ImageDraw

from auoreo.core.permissions import PermissionEngine
from auoreo.core.services import Services
from auoreo.gateway.types import ToolCall
from auoreo.harness.manifests import Rule
from auoreo.harness.orchestrator import build_spec
from auoreo.tools.context import ToolContext, ToolResult

ALLOW = [Rule(permission="*", pattern="*", action="allow")]
PERSON = (20, 20, 60, 120)  # tall solid block → `person`, score 1.0
CRATE = (150, 100, 230, 160)  # wide solid block → `object`, score 1.0
DISC = (250, 30, 300, 80)  # disc → `object`, score ≈ 0.8


def scene_png() -> bytes:
    img = Image.new("RGB", (320, 240), (20, 20, 30))
    d = ImageDraw.Draw(img)
    d.rectangle(PERSON, fill=(240, 80, 60))
    d.rectangle(CRATE, fill=(40, 160, 255))
    d.ellipse(DISC, fill=(255, 220, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def faces_png() -> bytes:
    """Two square 'faces' (one disc, one square) on a plain background."""
    img = Image.new("RGB", (240, 160), (230, 230, 230))
    d = ImageDraw.Draw(img)
    d.rectangle((20, 30, 90, 100), fill=(200, 140, 110))
    d.ellipse((140, 40, 200, 100), fill=(120, 80, 60))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def write_png(dir_: Path, name: str, data: bytes) -> Path:
    p = dir_ / name
    p.write_bytes(data)
    return p


def ctx_for(
    svc: Services, workdir: Path, session_id: str = "s1", rules: list[Rule] | None = None
) -> ToolContext:
    spec = build_spec(svc.harness, "default", "default", "manual")
    engine = PermissionEngine(
        list(ALLOW if rules is None else rules), plans_dir=str(workdir / ".auoreo" / "plans")
    )
    return ToolContext(
        session_id=session_id,
        cwd=workdir,
        home=svc.settings.home,
        harness=svc.harness,
        services={"memory": svc.memory, "services": svc, "engine": engine, "spec": spec},
    )


async def call(svc: Services, ctx: ToolContext, name: str, args: dict) -> ToolResult:
    out = await svc.coordinator.runner.executor.run(
        ToolCall("c1", name, json.dumps(args)),
        tools=svc.harness.tools,
        ctx=ctx,
        engine=ctx.services["engine"],
    )
    return out.result
