"""H5: coding/ops tool gaps — apply_patch format step, forecast metric store, run_code,
video_analyze, the workflow tool, the computer-use lock and the grep fallback.

Attached to `Settings` as `h5: H5Settings`; set it in `settings.json`
(`{"h5": {"apply_patch": {"format": false}}}`) or as a JSON env var (`AUOREO_H5='{...}'`).
Secrets never live here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ApplyPatchFormat(_Cfg):
    format: bool = True
    """Run the project's formatter on touched files after a patch (fail-open). A formatter
    only runs when the project opts in: ruff (`[tool.ruff]` / `ruff.toml`), prettier in
    `node_modules/.bin`, rustfmt next to a `Cargo.toml`."""
    format_timeout_s: float = 20.0


class ForecastSettings(_Cfg):
    max_samples: int = Field(default=2000, ge=10, le=100_000)
    """Samples kept per metric in `<home>/ops_metrics.db` (oldest pruned)."""
    record_from_ops_tools: bool = True
    """ops_cpu/ops_mem/ops_disk append each reading to the metric store."""
    alpha: float = Field(default=0.3, gt=0, le=1)
    """EWMA smoothing factor."""


class RunCodeSettings(_Cfg):
    timeout_s: int = Field(default=30, ge=1, le=600)
    max_output_chars: int = Field(default=20_000, ge=1000, le=1_000_000)


class VideoSettings(_Cfg):
    max_bytes: int = 50 * 2**20
    warn_bytes: int = 20 * 2**20
    timeout_s: float = 180.0
    max_frames: int = Field(default=8, ge=1, le=32)


class WorkflowToolSettings(_Cfg):
    timeout_s: float = 900.0
    max_depth: int = Field(default=2, ge=1, le=5)
    """Nesting cap: a workflow session may start another workflow at most this deep."""


class ComputerUseSettings(_Cfg):
    lock: bool = True
    """Hold `<home>/computer-use.lock` (O_EXCL, stale-PID recovery) while an action runs."""


class GrepSettings(_Cfg):
    python_fallback: bool = True
    """Search in pure Python (respects .gitignore, skips binaries) when `rg` is missing."""
    max_file_bytes: int = 4 * 2**20
    max_files: int = 50_000


class H5Settings(_Cfg):
    apply_patch: ApplyPatchFormat = Field(default_factory=ApplyPatchFormat)
    forecast: ForecastSettings = Field(default_factory=ForecastSettings)
    run_code: RunCodeSettings = Field(default_factory=RunCodeSettings)
    video: VideoSettings = Field(default_factory=VideoSettings)
    workflow: WorkflowToolSettings = Field(default_factory=WorkflowToolSettings)
    computer_use: ComputerUseSettings = Field(default_factory=ComputerUseSettings)
    grep: GrepSettings = Field(default_factory=GrepSettings)


def h5_of(ctx: object) -> H5Settings:
    """The live `settings.h5` from a ToolContext (defaults when no services are wired)."""
    svc = getattr(ctx, "services", {}).get("services")
    h5 = getattr(getattr(svc, "settings", None), "h5", None)
    return h5 if isinstance(h5, H5Settings) else H5Settings()
