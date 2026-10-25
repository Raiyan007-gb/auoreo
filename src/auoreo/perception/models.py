"""Model licence rules and cache helpers shared by the real perception backends.

RF-DETR N/S/M/L are Apache-2.0; XL/2XL weights are PML-1.0 and banned (spec L131). Weights
are only downloaded when `settings.perception_download_models` is true; otherwise they must
already be cached and `check()` says where it looked.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any

from auoreo.backends import BackendStatus

RFDETR_SIZES: dict[str, tuple[str, str]] = {
    "nano": ("RFDETRNano", "rf-detr-nano.pth"),
    "small": ("RFDETRSmall", "rf-detr-small.pth"),
    "medium": ("RFDETRMedium", "rf-detr-medium.pth"),
    "large": ("RFDETRLarge", "rf-detr-large.pth"),
}
RFDETR_BANNED = {"xlarge", "2xlarge", "xl", "2xl"}
PML_REASON = (
    "RF-DETR XL/2XL weights are licensed PML-1.0 (not Apache-2.0) and are banned; use nano|small|medium|large"
)


class ModelRefused(ValueError):
    """A model that the licence posture forbids (message says why)."""


def rfdetr_size(settings: Any) -> tuple[str, str, str]:
    """`(size, class_name, weights_file)` for `settings.detection_model`; raises `ModelRefused`."""
    size = str(getattr(settings, "detection_model", "nano") or "nano").lower().removeprefix("rf-detr-")
    if size in RFDETR_BANNED:
        raise ModelRefused(f"detection_model '{size}': {PML_REASON}")
    if size not in RFDETR_SIZES:
        raise ModelRefused(f"detection_model '{size}' is unknown; use one of {sorted(RFDETR_SIZES)}")
    cls, weights = RFDETR_SIZES[size]
    return size, cls, weights


def rfdetr_cache() -> Path:
    """Where rfdetr caches bare-filename weights (`RF_HOME` → `ROBOFLOW_HOME` → ~/.roboflow/models)."""
    root = os.environ.get("RF_HOME") or os.environ.get("ROBOFLOW_HOME")
    return Path(root).expanduser() if root else Path.home() / ".roboflow" / "models"


def models_dir(settings: Any) -> Path:
    """AUOREO's own model cache (`<home>/models/perception`) for single-file weights."""
    return Path(getattr(settings, "home", Path.home() / ".auoreo")) / "models" / "perception"


def download_allowed(settings: Any) -> bool:
    return bool(getattr(settings, "perception_download_models", False))


def missing(*modules: str) -> list[str]:
    """Top-level modules that cannot be imported (checked without importing them)."""
    return [m for m in modules if importlib.util.find_spec(m) is None]


def not_installed(mods: list[str], install: str) -> BackendStatus:
    return BackendStatus(False, f"not installed: {', '.join(mods)}", remedy=install)


DOWNLOAD_REMEDY = 'set "perception_download_models": true in settings.json (or pre-fetch the weights)'
