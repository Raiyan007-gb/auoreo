"""HTTP client for the isolated YOLO sidecar (AGPL code stays in its own process).

Contract (docs/perception.md "YOLO sidecar"):

- `GET  {url}/health` → `{"ok": true, "model": "yolo26n", "license": "AGPL-3.0", "labels": [...]}`
- `POST {url}/detect` with `{"image_b64": "<png/jpeg>", "classes": [...], "threshold": 0.3}`
  → `{"boxes": [{"label": "person", "score": 0.9, "x0": 1, "y0": 2, "x1": 3, "y1": 4}]}`

The sidecar is configured by the user (`yolo_sidecar_url`), normally on loopback.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

import httpx

from auoreo.backends import BackendStatus
from auoreo.backends.detection import Box, DetectionBackend
from auoreo.tools.context import ToolError

TIMEOUT_S = 30.0
REMEDY = 'run the sidecar (docs/perception.md) and set "yolo_sidecar_url": "http://127.0.0.1:8765"'


class Backend(DetectionBackend):
    name = "yolo_onnx"
    transport: httpx.AsyncBaseTransport | None = None
    """Test seam (`httpx.MockTransport`)."""

    @property
    def url(self) -> str | None:
        raw = getattr(self.settings, "yolo_sidecar_url", None)
        return str(raw).rstrip("/") if raw else None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=TIMEOUT_S, transport=self.transport, trust_env=False)

    async def check(self) -> BackendStatus:
        if self.url is None:
            return BackendStatus(
                False, "yolo_sidecar_url is not set (AGPL sidecar not configured)", remedy=REMEDY
            )
        try:
            async with self._client() as client:
                res = await client.get(f"{self.url}/health")
            body: dict[str, Any] = res.json() if res.status_code == 200 else {}
        except (httpx.HTTPError, ValueError) as exc:
            return BackendStatus(
                False, f"sidecar at {self.url} unreachable ({type(exc).__name__})", remedy=REMEDY
            )
        if not isinstance(body, dict) or not body.get("ok"):
            return BackendStatus(
                False, f"sidecar at {self.url} not healthy (HTTP {res.status_code})", remedy=REMEDY
            )
        return BackendStatus(True, f"sidecar {body.get('model', 'yolo')} at {self.url} (isolated process)")

    async def detect(self, image: bytes, *, classes: Sequence[str] = (), threshold: float = 0.3) -> list[Box]:
        if self.url is None:
            raise ToolError(f"yolo_sidecar_url is not set; {REMEDY}")
        payload = {
            "image_b64": base64.b64encode(image).decode("ascii"),
            "classes": [c for c in classes if c.strip()],
            "threshold": threshold,
        }
        try:
            async with self._client() as client:
                res = await client.post(f"{self.url}/detect", json=payload)
            res.raise_for_status()
            rows = res.json().get("boxes", [])
            return [
                Box(
                    str(r["label"]),
                    float(r["score"]),
                    float(r["x0"]),
                    float(r["y0"]),
                    float(r["x1"]),
                    float(r["y1"]),
                )
                for r in rows
            ]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ToolError(f"YOLO sidecar request failed ({type(exc).__name__})") from None
