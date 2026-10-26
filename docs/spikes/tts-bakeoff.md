# Spike 7: TTS bake-off

**Status:** the candidates are implemented as `tts` backends with license postures; the
listening test and latency/cost table are **not run here** (no audio output, no paid keys in
the dev container; quality needs human raters).

## Candidates in the tree (`backends/tts/`)

| Backend | Where it runs | License / posture | Notes |
|---|---|---|---|
| `kittentts` (default) | local CPU, `nano-int8` | Apache-2.0 / ok | smallest local model; installed from the GitHub wheel |
| `openai` | API (`gpt-4o-mini-tts`) | API / ok | ≤ 4096 chars per call, mp3 |
| `elevenlabs` | API (`eleven_flash_v2_5`, opus 48k) | API / ok | lowest-latency hosted option per vendor docs |
| `piper` | subprocess only | GPL-3.0 / gpl-isolated | stub: must never be imported |
| `edge_tts` | out-of-process CLI | LGPL-3.0 / gpl-isolated | unofficial service, opt-in |
| `sidecar` | `auoreo voice-host` (GPU venv) | follows the inner backend | moves heavy models out of the server |
| `fake` | in-process | MIT | 440 Hz tone; used by every test and e2e |

## Rubric (to fill in)

time-to-first-audio (p50/p95), real-time factor on CPU, MOS-style rating (5 raters × 20
prompts), cost per 1k characters, license, install weight.

## Measured here

Only correctness: every backend's request shape / subprocess contract is unit-tested and the
fake drives the voice e2e flows (meetings, barge-in).

## Decision

Keep `kittentts` as the local default (Apache, CPU) and the hosted lanes opt-in by key. The
bake-off numbers above decide any change; record them in this file with the hardware used.
