# Spikes (M6, workstream F7)

Each spike is a decision record: context, options, measurements taken in the dev container,
decision, follow-ups. Each one has a reproducible script or prototype checked in. Figures marked
"documented" come from vendor docs; everything else was measured.

| Spike | Spec | Decision | Artefacts |
|---|---|---|---|
| [Image generation](imagegen.md) | L849 | Plugin only, off by default, approval-gated; hosted (OpenAI `gpt-image-1`, fal FLUX) + `fake`; local SD not bundled | `plugins/imagegen/`, `tests/unit/test_imagegen_plugin.py` |
| [RL-vision / sensor policy](rl-vision.md) | L604 | No RL/bandit for perception yet; retuning the static default is worth +0.26 reward offline, the contextual bandit adds +0.04 with a reward that is not observable online | `scripts/spikes/rl_vision.py` |
| [HTTP/3](http3.md) | L1043 | Stay on uvicorn; terminate h2/h3 at Caddy/CDN. Hypercorn h3 incl. SSE + WS works but adds a second stack | `scripts/spikes/http3_probe.py`, `scripts/spikes/http3_caddy_e2e.sh`, `deploy/caddy/Caddyfile` |
| [Nuitka](nuitka.md) | L1095 | PyInstaller stays; the Nuitka onefile did not finish within the time box (litellm optimizer crashes, 7,175 modules to compile in C); kept as an experimental script | `scripts/build_binary_nuitka.py`, `scripts/spikes/auoreo.nuitka-package.config.yml` |
| [Tauri](tauri.md) | L1055 | Electron stays; the size win does not dominate once the ~200 MB server is bundled, and signing is unchanged | `desktop-tauri/` |
| [ACP changelog](acp.md) | L1039 | Stay on ACP v1; added `session/list`, `session/resume`, `session/close` + `sessionCapabilities` | `src/auoreo/api/routers/acp.py`, `tests/integration/test_acp_sessions.py` |
| [Rerank / embed / chunk / fusion](rerank-embed.md) | §22-2 | RRF-60 + 512/50 + optional embedding, rerank off; golden-set CI gate; model bake-off **not run here** (keys/GPU) | `scripts/eval_memory.py`, `evals/memory/` |
| [Perception stack](perception-stack.md) | §22-5 | Fakes stay default; licence posture gates selection (AGPL sidecar-only, NC opt-in); accuracy numbers **not run here** (hardware) | `backends/{diarize,detection,face}/` |
| [WebRTC transport](webrtc-transport.md) | §22-6 | `aiortc` default for loopback/LAN, LiveKit for WAN/SIP; WAN/CGNAT matrix **not run here** | `backends/rtc_transport/`, `deploy/{livekit,coturn}/` |
| [TTS bake-off](tts-bakeoff.md) | spike 7 | `kittentts` local default, hosted lanes by key; listening test **not run here** | `backends/tts/` |
