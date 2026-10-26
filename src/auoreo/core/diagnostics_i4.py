"""I4 doctor rows: workspace trust for `<cwd>/.auoreo` and the memory re-embed job."""

from __future__ import annotations

import os
from pathlib import Path

from auoreo.core.diagnostics import Check
from auoreo.core.settings import Settings
from auoreo.harness import trust as trust_mod


def workspace_row(st: Settings, cwd: Path | None = None) -> Check:
    ws = cwd or Path.cwd()
    if not (ws / ".auoreo").is_dir():
        return Check("workspace_trust", "ok", f"no project .auoreo in {ws}")
    if trust_mod.is_trusted(st.home, ws):
        how = " (AUOREO_SECURITY_TRUST_ALL_WORKSPACES)" if trust_mod.trust_all() else ""
        return Check("workspace_trust", "ok", f"trusted{how}: {os.path.realpath(ws)}")
    skipped = trust_mod.skipped_in(ws)
    if not skipped:
        return Check("workspace_trust", "ok", "untrusted, inert definitions only (nothing skipped)")
    names = ", ".join(f"{k}/{n}" for k, n in skipped[:6]) + (" …" if len(skipped) > 6 else "")
    return Check(
        "workspace_trust",
        "warn",
        f"untrusted: {len(skipped)} project definitions skipped ({names})",
        "review .auoreo/ then run `auoreo trust`",
    )


async def i4_checks(st: Settings) -> list[Check]:
    return [workspace_row(st)]
