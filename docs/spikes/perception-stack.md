# Spike 5: diarization + detection + face + MQTT (spec §22-5)

**Status:** backends are implemented behind ABCs with fakes and license postures; accuracy and
latency comparisons are **not run here** (no GPU, no camera/microphone, no MQTT devices).

## What exists

| Kind | Backends (`backends/<kind>/`) | Default | Posture |
|---|---|---|---|
| diarize | `sherpa_onnx`, `pyannote` (community-1, GPU meeting mode), `fake` | `fake` (select a real one in `settings.backends`) | ok (Apache / MIT code; pyannote weights gated by HF terms) |
| detection | `rfdetr` (Apache, N/S/M/L only), `grounding_dino`, `yolo_onnx` (AGPL, sidecar-only), `fake` | `fake` (select a real one) | YOLO is `agpl-isolated` (separate process, never imported) |
| face | `mediapipe` (Apache), `sherpa_onnx`, `insightface` (NC weights), `fake` | `fake` (select a real one) | InsightFace is `nc-opt-in` |
| MQTT | `paho-mqtt` v2 client (`perception/`, smart-home sensors), in-process test broker (`tests/mqtt_broker.py`) | off | ok |
| miio | P2 | — | — |

Doctor prints the selected backend of each kind with readiness and posture; see
`docs/perception.md` and `docs/voice.md` (diarization section).

## Measured here

Unit/integration tests run every kind through its fake and the real code paths that do not
need hardware (sidecar protocol, posture enforcement, MQTT round-trips against the local
broker).

## Not run here

- DER of sherpa vs WhisperX + pyannote on meeting audio;
- mAP / latency of RF-DETR vs Grounding-DINO vs YOLO26-ONNX;
- face-match precision of MediaPipe vs sherpa vs InsightFace;
- real Zigbee/MQTT devices.

## Decision

The fakes stay the defaults (no model download at install); licence posture decides what may be
selected (Apache/MIT in-process; AGPL only as a sidecar; NC weights only behind an explicit
opt-in). Re-open the defaults once the hardware-bound numbers above
exist; record them in this file.
