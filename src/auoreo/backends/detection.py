"""Object / face detection backend kinds (`backends/detection/`, `backends/face/`, M6).

Defaults are Apache-licensed and local-only (RF-DETR, Grounding-DINO, MediaPipe). AGPL
models (YOLO) only via an isolated sidecar, never imported in-process; NC models
(InsightFace) only behind an explicit opt-in. Faces are default-off and face embeddings are
never persisted (spec §14.1, L131, L744).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from auoreo.backends import BackendStatus


@dataclass(frozen=True)
class Box:
    label: str
    score: float
    x0: float
    y0: float
    x1: float
    y1: float
    """Pixel coordinates in the input image."""


@dataclass(frozen=True)
class Face:
    box: Box
    landmarks: list[tuple[float, float]] = field(default_factory=list)
    embedding: list[float] | None = None
    """Only when explicitly requested; never written to disk or memory."""


class DetectionBackend(ABC):
    name: str = "detection"
    open_vocabulary: bool = False
    """True when `classes` may be free text (Grounding-DINO); else a fixed label set."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus: ...

    @abstractmethod
    async def detect(
        self, image: bytes, *, classes: Sequence[str] = (), threshold: float = 0.3
    ) -> list[Box]: ...


class FaceBackend(ABC):
    name: str = "face"

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    @abstractmethod
    async def check(self) -> BackendStatus: ...

    @abstractmethod
    async def faces(self, image: bytes, *, embeddings: bool = False) -> list[Face]: ...
