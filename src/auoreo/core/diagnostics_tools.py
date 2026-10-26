"""H5 doctor rows for external binaries the bundled tools lean on (`rg`, `ffmpeg`).

Neither is required: `grep` falls back to a pure-Python search and `video_analyze` refuses
honestly. The rows say what is degraded and how to fix it; nothing here installs anything.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auoreo.core.diagnostics import Check

BINARIES: dict[str, tuple[str, str]] = {  # binary → (degraded without it, remedy)
    "rg": (
        "grep uses the slower Python fallback; glob/codebase_map lose ripgrep speed",
        "install ripgrep (apt install ripgrep · brew install ripgrep · "
        "winget install BurntSushi.ripgrep.MSVC)",
    ),
    "ffmpeg": (
        "video_analyze is unavailable (frame sampling needs ffmpeg)",
        "install ffmpeg (apt install ffmpeg · brew install ffmpeg · winget install Gyan.FFmpeg)",
    ),
}


def tool_binary_checks() -> list[Check]:
    from auoreo.core.diagnostics import Check

    rows: list[Check] = []
    for name, (degraded, remedy) in BINARIES.items():
        path = shutil.which(name)
        if path:
            rows.append(Check(name, "ok", path))
        else:
            rows.append(Check(name, "warn", f"not found: {degraded}", remedy))
    return rows
