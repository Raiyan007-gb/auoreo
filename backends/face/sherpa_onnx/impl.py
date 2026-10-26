"""sherpa-face stub: honest `check()`, never pretends to detect (sherpa-onnx imported lazily)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from auoreo.backends import BackendStatus
from auoreo.backends.detection import Face, FaceBackend
from auoreo.tools.context import ToolError

INSTALL = "uv pip install sherpa-onnx"
STUB = "face/sherpa_onnx is a stub: no sherpa-onnx face pipeline is wired yet; use face/mediapipe"


class Backend(FaceBackend):
    name = "sherpa_onnx"
    supports_embeddings = False

    def _model_dir(self) -> Path | None:
        g6 = getattr(self.settings, "g6", None)
        raw = g6.voice.sherpa_face_model_dir if g6 is not None else None
        return Path(raw).expanduser() if raw else None

    async def check(self) -> BackendStatus:
        if importlib.util.find_spec("sherpa_onnx") is None:
            return BackendStatus(False, f"sherpa-onnx is not installed; {STUB}", remedy=INSTALL)
        folder = self._model_dir()
        if folder is None or not folder.is_dir():
            return BackendStatus(
                False,
                f"no face model folder (g6.voice.sherpa_face_model_dir); {STUB}",
                remedy='select "backends": {"face": "mediapipe"}',
            )
        return BackendStatus(False, STUB, remedy='select "backends": {"face": "mediapipe"}')

    async def faces(self, image: bytes, *, embeddings: bool = False) -> list[Face]:
        raise ToolError(STUB)
