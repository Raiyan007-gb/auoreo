"""InsightFace (NC-gated; lazy import). Only usable with `face_nc_opt_in: true`.

`FaceAnalysis(name="buffalo_l", root=<home>/models/perception, providers=["CPUExecutionProvider"])`
then `prepare(ctx_id=-1)`; `app.get(bgr_ndarray)` returns faces with `bbox`, `kps`,
`det_score` and `normed_embedding`. InsightFace downloads missing packs by itself, so the
pack must already exist unless `perception_download_models` is true. Embeddings are handed
back to the caller only; this backend never writes them anywhere.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

from auoreo.backends import BackendStatus
from auoreo.backends.detection import Box, Face, FaceBackend
from auoreo.perception.blobs import decode
from auoreo.perception.models import DOWNLOAD_REMEDY, download_allowed, missing, models_dir, not_installed
from auoreo.tools.context import ToolError

PACK = "buffalo_l"
INSTALL = "uv pip install insightface onnxruntime"
NC_WARNING = (
    "WARNING: InsightFace model weights are licensed for NON-COMMERCIAL research use only; "
    "you are responsible for compliance"
)
NC_REMEDY = 'set "face_nc_opt_in": true in settings.json only if your use is non-commercial'
_lock = threading.Lock()
_apps: dict[str, Any] = {}


class Backend(FaceBackend):
    name = "insightface"
    supports_embeddings = True

    @property
    def root(self) -> Path:
        return models_dir(self.settings)

    async def check(self) -> BackendStatus:
        if not getattr(self.settings, "face_nc_opt_in", False):
            return BackendStatus(False, f"refused: face_nc_opt_in is false. {NC_WARNING}", remedy=NC_REMEDY)
        if mods := missing("insightface", "onnxruntime", "numpy"):
            return not_installed(mods, INSTALL)
        pack = self.root / "models" / PACK
        if not pack.is_dir() and not download_allowed(self.settings):
            return BackendStatus(False, f"{PACK} not found at {pack}. {NC_WARNING}", remedy=DOWNLOAD_REMEDY)
        return BackendStatus(True, f"{PACK} (CPU). {NC_WARNING}")

    def _app(self) -> Any:
        if not getattr(self.settings, "face_nc_opt_in", False):
            raise ToolError(f"insightface refused: face_nc_opt_in is false. {NC_WARNING}")
        with _lock:
            if PACK not in _apps:
                if not (self.root / "models" / PACK).is_dir() and not download_allowed(self.settings):
                    raise ToolError(f"{PACK} is not downloaded; {DOWNLOAD_REMEDY}")
                from insightface.app import FaceAnalysis

                app = FaceAnalysis(name=PACK, root=str(self.root), providers=["CPUExecutionProvider"])
                app.prepare(ctx_id=-1)
                _apps[PACK] = app
            return _apps[PACK]

    def _run(self, image: bytes, embeddings: bool) -> list[Face]:
        try:
            app = self._app()
            import numpy as np
        except ImportError:
            raise ToolError(f"insightface is not installed; {INSTALL}") from None
        bgr = np.asarray(decode(image))[:, :, ::-1]
        out: list[Face] = []
        for f in app.get(bgr):
            x0, y0, x1, y1 = (float(v) for v in f.bbox)
            kps = getattr(f, "kps", None)
            marks = [(float(x), float(y)) for x, y in kps] if kps is not None else []
            emb = (
                [float(v) for v in f.normed_embedding]
                if embeddings and f.normed_embedding is not None
                else None
            )
            out.append(Face(Box("face", float(f.det_score), x0, y0, x1, y1), marks, emb))
        return out

    async def faces(self, image: bytes, *, embeddings: bool = False) -> list[Face]:
        return await asyncio.to_thread(self._run, image, embeddings)
