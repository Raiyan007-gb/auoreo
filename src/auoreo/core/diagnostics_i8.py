"""I8 doctor rows (registered into `diagnostics.EXTRA_CHECKS`):

- `desktop:health` — the selected desktop backend's `health_report()` (exit-code semantics:
  0 ok, 1 degraded → warn, 2 unavailable → warn; `display_count=0` is unavailable; on macOS
  cua-driver needs Accessibility + Screen Recording);
- `livekit:worker` — only when `i8.livekit.worker.enabled`: is `livekit-agents` importable, plus
  the licence posture note (framework Apache-2.0, turn-detector weights under LiveKit's
  MODEL_LICENSE — not used by the AUOREO tap, which keeps AUOREO's own endpointing).
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

from auoreo.core.paths import bundled_root
from auoreo.core.settings_i8 import i8_of
from auoreo.harness.discovery import default_roots
from auoreo.harness.registry import Harness, HarnessError

if TYPE_CHECKING:
    from auoreo.core.diagnostics import Check, Level
    from auoreo.core.settings import Settings


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


async def i8_checks(st: Settings) -> list[Check]:
    from auoreo.core.diagnostics import Check

    out: list[Check] = []
    try:
        h = Harness(default_roots(st.home, Path.cwd(), bundled_root(), st.extra_roots))
        bdef = h.select_backend("desktop", st.backends.get("desktop"))
        if bdef is not None:
            rep = await asyncio.wait_for(bdef.load()(st).health_report(), 20)
            level: Level = "ok" if rep.code == 0 else "warn"
            perms = ""
            if rep.accessibility is not None or rep.screen_recording is not None:
                perms = f"; accessibility={rep.accessibility} screen_recording={rep.screen_recording}"
            detail = (
                f"{bdef.manifest.name}: exit {rep.code} · displays {rep.display_count} · {rep.detail}{perms}"
            )
            fix = "" if rep.code == 0 else "see docs/teleop.md (computer use: permissions, a desktop session)"
            out.append(Check("desktop:health", level, detail, fix))
    except (HarnessError, KeyError, TimeoutError) as exc:
        out.append(Check("desktop:health", "warn", f"health_report failed: {exc}"))
    if i8_of(st).livekit.worker.enabled:
        if not _has("livekit.agents"):
            out.append(
                Check(
                    "livekit:worker",
                    "warn",
                    "livekit-agents is not installed (not an extra: it conflicts with the otel extra)",
                    "uv pip install 'livekit-agents>=1.8,<2'",
                )
            )
        else:
            note = "livekit-agents Apache-2.0; turn-detector weights are MODEL_LICENSE (unused here)"
            out.append(Check("livekit:worker", "ok", note))
    return out
