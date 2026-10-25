"""Fake face backend: roughly square colour blobs are "faces" (deterministic, Pillow only).

Landmarks are the 5 conventional points (eyes, nose, mouth corners) placed inside the box;
an embedding (only when asked) is a unit vector derived from the blob's colour and shape.
Nothing is stored anywhere.
"""

from __future__ import annotations

import asyncio
import math

from auoreo.backends import BackendStatus
from auoreo.backends.detection import Box, Face, FaceBackend
from auoreo.perception.blobs import Blob, decode, find_blobs

ASPECT = (0.7, 1.45)
DIMS = 16


def _landmarks(b: Blob) -> list[tuple[float, float]]:
    w, h = b.x1 - b.x0, b.y1 - b.y0
    rel = ((0.3, 0.38), (0.7, 0.38), (0.5, 0.58), (0.35, 0.78), (0.65, 0.78))
    return [(round(b.x0 + w * fx, 1), round(b.y0 + h * fy, 1)) for fx, fy in rel]


def _embedding(b: Blob) -> list[float]:
    seed = [c / 255 for c in b.color] + [b.aspect, b.score]
    vec = [math.sin((i + 1) * (1 + sum(v * (j + 1) for j, v in enumerate(seed)))) for i in range(DIMS)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def _faces(image: bytes, embeddings: bool) -> list[Face]:
    out: list[Face] = []
    for b in find_blobs(decode(image)):
        if not ASPECT[0] <= b.aspect <= ASPECT[1]:
            continue
        box = Box("face", b.score, b.x0, b.y0, b.x1, b.y1)
        out.append(Face(box, _landmarks(b), _embedding(b) if embeddings else None))
    return out


class Backend(FaceBackend):
    name = "fake"
    is_test_backend = True
    supports_embeddings = True

    async def check(self) -> BackendStatus:
        return BackendStatus(True, "fake face detector (test backend; square colour blobs)")

    async def faces(self, image: bytes, *, embeddings: bool = False) -> list[Face]:
        return await asyncio.to_thread(_faces, image, embeddings)
