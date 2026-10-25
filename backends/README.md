# backends/

A backend implements one capability *kind* (screen capture, browser automation, speech to
text, …) so the engine and tools never depend on a specific library or device.

```
backends/<kind>/<name>/backend.yaml
backends/<kind>/<name>/impl.py
```

```yaml
kind: capture                  # must match the parent folder
name: mss                      # must match this folder
description: Screen capture with the mss library.
impl: impl.py:Backend          # Backend(settings) with `async def check(self) -> BackendStatus`
default: true                  # at most one default per kind
requires_binaries: []          # executables that must be on PATH (doctor reports them)
requires_env: []
license: MIT
posture: ok                    # ok | gpl-isolated | nc-opt-in (surfaced by doctor)
```

The ABC for each kind lives in `src/auoreo/backends/<kind>.py`; `impl.py` subclasses it.
Tool handlers get the selected instance with `ctx.backend("capture")`. Selection order:
`settings.backends[kind]` → the manifest marked `default: true` → the only candidate.
Every kind ships a `fake` backend used by tests and by `AUOREO_GATEWAY=fake` demos;
`auoreo doctor` runs `check()` on the selected backend of every kind.

Kinds today: `capture`, `browser`, `sandbox` (none · docker · bwrap; `sandbox_policy:
warn|fail` decides what happens when the selected one is unavailable), `stt`, `tts`,
`vad`, `wake`, `desktop`, `rerank` (none · fake · cross_encoder · cohere; fail-open), `environment` (local · docker ·
ssh · e2b · modal · daytona), `detection` / `face` / `sensor` (fake defaults; faces off unless
`faces_enabled`), `rtc_transport`, `diarize` (fake · sherpa_onnx · pyannote). Real voice/desktop libraries are imported lazily; their
`check()` reports what is missing, and the `fake` sibling keeps tests and demos keyless.
