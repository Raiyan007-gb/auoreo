"""Fake detector: colour blobs → `object` / `person` boxes (deterministic, Pillow only)."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from auoreo.backends import BackendStatus
from auoreo.backends.detection import Box, DetectionBackend
from auoreo.perception.blobs import decode, find_blobs

LABELS = ("object", "person")
PERSON_ASPECT = 1.6


def _detect(image: bytes, classes: Sequence[str], threshold: float) -> list[Box]:
    wanted = {c.strip().lower() for c in classes if c.strip()}
    out: list[Box] = []
    for b in find_blobs(decode(image)):
        label = "person" if b.aspect >= PERSON_ASPECT else "object"
        if b.score < threshold or (wanted and label not in wanted):
            continue
        out.append(Box(label, b.score, b.x0, b.y0, b.x1, b.y1))
    return out


class Backend(DetectionBackend):
    name = "fake"
    is_test_backend = True

    async def check(self) -> BackendStatus:
        return BackendStatus(True, f"fake colour-blob detector (test backend; labels {list(LABELS)})")

    async def detect(self, image: bytes, *, classes: Sequence[str] = (), threshold: float = 0.3) -> list[Box]:
        return await asyncio.to_thread(_detect, image, classes, threshold)
