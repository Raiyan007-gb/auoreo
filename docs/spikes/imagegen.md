# Spike: image generation (spec L849)

**Decision:** ship image generation only as the out-of-process plugin `plugins/imagegen/`,
**off by default**, with hosted backends (OpenAI `gpt-image-1`, fal FLUX) plus a `fake`
placeholder. Local Stable Diffusion is not bundled and not a backend in this spike. Nothing goes
into core or the default tool pool.

## Context

The spec puts image generation outside core: if it exists at all, it is a plugin toolset behind
approval and a cost ledger, never in the default pool, and the open question was
"fal/openai-images vs local SD".

## What was built

`plugins/imagegen/` (`plugin.yaml`, `pyproject.toml`, `plugin.py`), the same layout as
`plugins/telegram` and `plugins/mqtt`:

- Tool `imagegen_generate {prompt, size ∈ 1024x1024|1024x1536|1536x1024, n ≤ 2}`. The host
  registers it as `plugin__imagegen__imagegen_generate`. `toolset: plugin:imagegen` and
  `exposure: deferred`, so it never reaches the default pool, and `default_action: ask`
  (`src/auoreo/plugins/bridge.py`), so every call needs approval unless a rule allows it.
- `provides: [tools]`, `consumes: ["secrets:OPENAI_API_KEY", "secrets:FAL_KEY"]`, and
  `enabled: false` in the manifest. The host only starts it when the manifest says
  `enabled: true` and `settings.plugins_enabled` is on.
- Backends are picked by plugin config `backend`:
  - `openai`: `POST /v1/images/generations` with `{model: gpt-image-1, prompt, size, n,
    quality}` and a Bearer key. Images come back as `data[].b64_json`; `usage` tokens are used
    for the cost.
  - `fal`: synchronous `POST https://fal.run/<fal_model>` (default `fal-ai/flux/schnell`) with
    `Authorization: Key …` and `{prompt, image_size: {width, height}, num_images}`. The
    `images[].url` files are downloaded, and only `https:` URLs are accepted.
  - `fake`: a Pillow-drawn card at 1/4 resolution. It needs no network and costs $0. It is the
    default and the test backend.
- Output goes to `<output_dir>/<session_id>/imagegen/<stamp>-<i>.png`. The default
  `output_dir` is `~/.auoreo/sessions`. The session id is sanitised, so it cannot escape the
  folder. Only PNG, JPEG and WebP files are written; the format is checked by magic bytes, so
  SVG and HTML are refused. The tool result is JSON
  `{images: [{path, media_type, bytes}], backend, model, usd_estimate, cost_basis}`.
- Cost: every call is recorded in three places:
  1. in the tool result, which is persisted in the events table;
  2. as an `imagegen.cost {…}` line on the host log;
  3. as a row appended to `costs.jsonl` next to the images.

### Gaps found in the host (not fixed here: `src/auoreo/plugins/**` is out of scope)

1. **No usage or audit hook for plugins.** Plugins can make two calls into the host,
   `secret.get` and `pairing.request`, and send two notifications, `log` and
   `connector.message`. None of them reaches `UsageLedger` or `AuditLog`. So the
   `imagegen.cost` audit row the brief asked for cannot be written from a plugin without
   editing the host. Proposed follow-up: a `usage.record {usd, model, purpose}` plugin → host
   request. The host would check it against a manifest `consumes: [usage]` and write a
   `usage` row with `purpose: plugin:imagegen`, plus an `imagegen.cost` audit row.
2. **Tool results are text only.** `tool.call` answers `{content: str, is_error}`, so a
   plugin cannot return an image part. The model gets the file paths. The `read` tool or a
   vision tool can attach an image afterwards. Proposed follow-up: an optional
   `images: [{path, media_type}]` field in the reply, which the host would turn into
   `ToolResult.images` (with an allowlist under the session folder).
3. **Plugins do not see `AUOREO_HOME`.** The child environment is scrubbed, hence the
   `output_dir` config. A host-provided `session_dir` in `tool.call` would remove that.

## Options compared

| | OpenAI `gpt-image-1` | fal FLUX.1 [schnell] | Local SD (SDXL / FLUX on own GPU) |
|---|---|---|---|
| Price per 1024² image | low $0.011 · medium $0.042 · high $0.167 (documented by OpenAI; token-priced: $5/M input, $40/M image output) | ≈ $0.003/MP ≈ $0.0031 (documented by fal) | $0 marginal; GPU and power instead |
| Latency | seconds to tens of seconds (documented; not measured here, no key) | about 1–2 s for schnell (documented by fal; not measured) | about 2–10 s on a modern NVIDIA GPU for SDXL 1024² (documented by model and community benchmarks); minutes on CPU |
| Setup | API key | API key | 7–24 GB of weights, CUDA/ROCm/MPS, diffusers + torch (≈ 2–3 GB of wheels) |
| Hardware | none | none | SDXL fp16: about 8 GB VRAM minimum, 12 GB comfortable; FLUX.1 schnell bf16: about 24 GB (fp8 about 12 GB) (documented by model cards) |
| Licence | API terms | FLUX.1 schnell Apache-2.0 | SDXL: CreativeML Open RAIL++-M; FLUX.1 dev: non-commercial |
| Privacy | prompt leaves the machine | prompt leaves the machine | local |
| Fits AUOREO's packaging | yes (httpx in the plugin venv) | yes | no: torch is excluded from the binary (`collect.EXCLUDED_MODULES`), the plugin venv would be several GB, and this container has no GPU |

## Measurements (this container)

- No `OPENAI_API_KEY` or `FAL_KEY` in this container, so the hosted backends were **not called
  live**. They are covered by contract tests on the recorded request shapes
  (`tests/unit/test_imagegen_plugin.py`, `httpx.MockTransport`). The tests check the URL,
  method, auth header, the exact JSON body, response parsing, the fal download, and the cost
  maths for tokens, per-image fallback, per-megapixel and config override.
- Fake backend, in process: median **4.2 ms** per call for `n=2, 1536x1024` over 20 calls.
  The outputs are two 384×256 PNGs of about 2.5 KB each.
- Local SD: not run (no GPU; requirements above are documented, not measured).
- Tests: 13 passed (`tests/unit/test_imagegen_plugin.py`).

## Decision

1. Use hosted backends behind the plugin. They are cheap per image, need no hardware and fit
   the plugin venv model. FLUX schnell on fal is about 3.5× cheaper than `gpt-image-1` at
   `low` and much cheaper than `high`. `gpt-image-1` follows prompts and renders text better.
   Both stay available, and `fake` is the default.
2. Local SD is not a backend for now. It belongs in a separate `imagegen-local` plugin. That
   plugin should talk to an already-running ComfyUI or Automatic1111 HTTP server on the user's
   GPU box, so torch never enters AUOREO's environments. Build it only when a user with a GPU
   asks for it.
3. Keep it off by default and approval-gated; the cost estimate is in every result.

## Follow-ups

- Host: `usage.record` plugin request + `imagegen.cost` audit row (gap 1); image parts in
  `tool.call` replies (gap 2); `session_dir` in `tool.call` (gap 3).
- Per-session spend cap: refuse when the sum in `costs.jsonl` exceeds `max_usd_per_session`.
  This is easy once gap 1 lands in the ledger.
- Live smoke with real keys (user checkpoint): one `low` gpt-image-1 call and one fal schnell
  call, then compare `usd_estimate` with the provider's billing.
