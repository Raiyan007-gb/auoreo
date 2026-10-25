# Perception: detection, faces and sensors (W2-C)

Three backend kinds and three deferred tools. Every real backend imports its library
lazily, `check()` says exactly what is missing, and a `fake` sibling (the default) keeps
tests and `AUOREO_GATEWAY=fake` demos keyless.

| Tool | Permission (default) | What it returns |
|---|---|---|
| `detect_objects` | `perception` (allow; local paths also pass the `read` rules, the screen the `screen` rule) | one line per box (`label score [x0, y0, x1, y1]`) + the image with boxes drawn |
| `detect_faces` | `faces` (ask) — refused unless `faces_enabled: true` | face boxes; `landmarks`, memory-only embedding refs + similarities on request |
| `sensor_read` | `sensor` (ask), pattern = `source` | `presence`, `reading`, `confidence` per sample (+ the latest frame for cameras) |

## Backends and licence posture

Select with `settings.backends.{detection,face,sensor}`; `auoreo doctor` shows one row per
kind with the posture tag (`[agpl-isolated]`, `[nc-opt-in]`).

| Kind | Name | Library (install) | Licence | Posture | Notes |
|---|---|---|---|---|---|
| detection | `fake` (default) | Pillow | MIT | ok | colour blobs → `object` / `person` (≥ 1.6× taller than wide) |
| detection | `rfdetr` | `rfdetr` (pulls torch) | Apache-2.0 | ok | `detection_model`: `nano`·`small`·`medium`·`large`; **XL/2XL are PML-1.0 and refused** |
| detection | `grounding_dino` | `transformers` + `torch` | Apache-2.0 | ok | `IDEA-Research/grounding-dino-tiny`, open vocabulary (`classes` are phrases) |
| detection | `yolo_onnx` | none in-process (HTTP client) | AGPL-3.0 (sidecar) | agpl-isolated | talks to a separate sidecar; **never `import ultralytics`** |
| face | `fake` (default) | Pillow | MIT | ok | square blobs, 5 synthetic landmarks, deterministic embedding |
| face | `mediapipe` | `mediapipe` | Apache-2.0 | ok | BlazeFace short-range, local-only, no embeddings |
| face | `insightface` | `insightface` + `onnxruntime` | NC weights | nc-opt-in | `check()` fails unless `face_nc_opt_in: true`; doctor prints the NC warning |
| sensor | `fake` (default) | Pillow | MIT | ok | `source` `script:1,0,1` scripts presence; generated frame |
| sensor | `uvc` | `opencv-python-headless` | Apache-2.0 | ok | `source` = device index (`"0"`, `/dev/video2`) |
| sensor | `rtsp` | `opencv-python-headless` | Apache-2.0 | ok | `source` = `rtsp://` / `rtsps://` URL; read-only, no PTZ |
| sensor | `rtc` | — | MIT | ok | latest WebRTC video frame of a session (`source: "current"`) |
| sensor | `ble` | — | MIT | ok | stub: `check()` says so; bridge BLE through MQTT |

Banned outright: `face_recognition`/dlib, RF-DETR XL/2XL, any in-process AGPL import
(a test walks every module's imports). MQTT topics are handled by the mqtt plugin, not here.

The optional libraries are not a `pyproject` extra (they pull torch/CUDA-sized wheels);
install the ones you need into the AUOREO environment, e.g.
`uv pip install rfdetr`, `uv pip install transformers torch`, `uv pip install mediapipe`,
`uv pip install opencv-python-headless`.

### Model weights

Nothing is downloaded unless `perception_download_models: true`. Otherwise:

- RF-DETR looks for `rf-detr-<size>.pth` in rfdetr's cache (`$RF_HOME`, else
  `~/.roboflow/models`) and passes it as an explicit path, which rfdetr never re-downloads.
- Grounding-DINO loads with `local_files_only=True` from the Hugging Face cache.
- MediaPipe/InsightFace read from `<home>/models/perception/`
  (`blaze_face_short_range.tflite`, `models/buffalo_l/`).

## Privacy rules (spec L744)

- **Faces are off by default.** `detect_faces` refuses until a user sets
  `faces_enabled: true`, and even then the `faces` permission asks.
- **Embeddings are never persisted.** Backends return them only when asked
  (`faces(embeddings=True)`). Tool results go into the transcript and the events table, so
  `detect_faces` does not put embeddings in its result: it keeps them in an in-memory
  vault (`auoreo.perception.faces`) under opaque refs (`face_ab12cd34`) and returns only
  the refs and cosine similarities. Refs expire after 10 minutes, are dropped on
  `forget: true` and when the session is archived/deleted, and vanish when the server
  stops. Nothing is written to memory, `state.db` or disk (tested by dumping both DBs).
- No identification: there is no gallery of known faces and no tool to build one.
- Camera frames (sensors, RTC sampling) live in memory only; images returned to the model
  follow the normal image pipeline (EXIF/metadata stripped).

## Sensor probes

`sensor_read` starts a probe on first use in a session and keeps it open for later reads
(so motion is measured between frames); it stops when the session is archived or deleted
(the same `session.archived` / `session.deleted` bus watch the persistent shell uses),
after 10 minutes idle, on `stop: true`, or at server shutdown. At most 4 probes per session.
Every start/stop is audited (`sensor.start` / `sensor.stop`).

RTSP URLs often embed credentials. Logs, audit records, tool output and error messages
show the URL with the userinfo replaced (`rtsp://***@cam.local/stream1`); the generic
event redactor masks `user:pass@` in tool arguments as well. Prefer allow rules like
`{permission: sensor, pattern: "rtsp://*@cam.local/*", action: allow}`.

Camera probes are read-only: frames are pulled, nothing is sent back to the device. PTZ or
any other camera control would be a separate tool behind its own approval.

### RTC video sampling (optional)

With `perception_sample_rtc_video: true`, a session's inbound WebRTC video track is sampled
every `perception_rtc_interval_s` seconds (default 5) instead of being drained; the frame
is JPEG-encoded into an in-memory store (16 sessions max, 2-minute staleness) that the
`rtc` probe reads: `sensor_read {"probe": "rtc", "source": "current"}`.

## YOLO sidecar

YOLO26n is AGPL-3.0. AUOREO never loads it in-process; `backends/detection/yolo_onnx` is
an HTTP client for a sidecar you run yourself in its own virtualenv/container. Set
`"yolo_sidecar_url": "http://127.0.0.1:8765"` and `"backends": {"detection": "yolo_onnx"}`.

Contract:

```
GET  /health  → 200 {"ok": true, "model": "yolo26n", "license": "AGPL-3.0", "labels": [...]}
POST /detect  {"image_b64": "<base64 PNG/JPEG>", "classes": ["person"], "threshold": 0.3}
              → 200 {"boxes": [{"label": "person", "score": 0.91, "x0": 1, "y0": 2, "x1": 30, "y1": 90}]}
```

Coordinates are pixels of the submitted image; an empty `classes` list means all labels.
A minimal sidecar (run separately, e.g. `python sidecar.py` in a venv with
`ultralytics onnxruntime fastapi uvicorn pillow`; distribute its source under AGPL):

```python
# sidecar.py — separate process, its own venv; never imported by AUOREO
import base64, io
from fastapi import FastAPI
from PIL import Image
from ultralytics import YOLO

app, model = FastAPI(), YOLO("yolo26n.onnx")


@app.get("/health")
def health():
    return {"ok": True, "model": "yolo26n", "license": "AGPL-3.0", "labels": list(model.names.values())}


@app.post("/detect")
def detect(req: dict):
    img = Image.open(io.BytesIO(base64.b64decode(req["image_b64"]))).convert("RGB")
    res = model.predict(img, conf=float(req.get("threshold", 0.3)), verbose=False)[0]
    wanted = set(req.get("classes") or [])
    boxes = []
    for (x0, y0, x1, y1), score, cls in zip(
        res.boxes.xyxy.tolist(), res.boxes.conf.tolist(), res.boxes.cls.tolist()
    ):
        label = model.names[int(cls)]
        if not wanted or label in wanted:
            boxes.append({"label": label, "score": score, "x0": x0, "y0": y0, "x1": x1, "y1": y1})
    return {"boxes": boxes}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)
```

Keep it on loopback (or a private network) — the sidecar has no auth of its own.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `detection_model` | `nano` | RF-DETR size (`nano`·`small`·`medium`·`large`) |
| `perception_download_models` | `false` | allow weight downloads on first use |
| `faces_enabled` | `false` | face detection opt-in |
| `face_nc_opt_in` | `false` | allow the NC-licensed InsightFace backend |
| `yolo_sidecar_url` | `null` | base URL of the AGPL YOLO sidecar |
| `perception_sample_rtc_video` | `false` | sample RTC video frames into the `rtc` probe |
| `perception_rtc_interval_s` | `5.0` | seconds between sampled RTC frames |
