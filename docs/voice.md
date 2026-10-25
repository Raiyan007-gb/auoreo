# Voice (M4c)

Voice is two planes on the one FastAPI port:

- **Control plane (in-process):** the voice service (`src/auoreo/voice/service.py`, started
  as the `voice` extension → `services.ext["voice"]`), `/api/voice/*`, the `transcribe` and
  `speak` tools, and `voice.*` events in the durable event log.
- **Media plane (WebRTC):** live microphone audio arrives on an aiortc peer
  (`/api/rtc/offer`, M4e). The RTC layer decodes it to 16 kHz mono int16 and hands every
  ~20 ms chunk to `RtcManager.on_audio_frames`; the voice bridge
  (`src/auoreo/voice/rtc_bridge.py`) installs that hook.

No audio library is imported by the server at startup. Every real backend imports its
library lazily inside the call that needs it and reports honestly from `check()` when the
library, model, key, binary or device is missing. Each kind ships a `fake` backend used by
the tests and keyless demos.

```
upload (wav/webm/ogg/mp3/m4a, ≤ 25 MB)
   └─ ffmpeg normalize → WAV 16 kHz mono s16 ─ VAD gate (silence → 422 "no speech detected")
                                                └─ STT backend → text (+ optional followup turn)

text (≤ 5000 chars) ─ prepare_spoken_text ─ sentence chunks ≤ chunk_limit ─ TTS backend
                                                  └─ concatenate (WAV exact; else first chunk's type)

WebRTC audio ─ on_audio_frames ─ VAD per chunk ─ speech ≥ 100 ms … silence ≥ 700 ms
                                     └─ utterance WAV → STT → voice.transcript → steer/followup
```

## Backends and license posture

`backends/<kind>/<name>/` (ABCs in `src/auoreo/backends/{stt,tts,vad,wake}.py`). `auoreo
doctor` prints the selected backend of every kind with its readiness and posture;
`GET /api/voice/status` returns the same matrix as JSON.

| Kind | Backend | Default | Needs | License | Posture |
|---|---|---|---|---|---|
| stt | `faster_whisper` | yes | `pip install faster-whisper`; model `base` cached or `voice_download_models` | MIT | ok |
| stt | `openai` | | `OPENAI_API_KEY` (gpt-4o-transcribe; whisper-1 gives segments) | API | ok |
| stt | `groq` | | `GROQ_API_KEY` (whisper-large-v3-turbo) | API | ok |
| stt | `fake` | | — (WAV `ICMT` comment or "fake transcript (N.N s)") | MIT | ok |
| tts | `kittentts` | yes | KittenTTS wheel; `nano-int8` model cached or `voice_download_models` | Apache-2.0 | ok |
| tts | `openai` | | `OPENAI_API_KEY` (gpt-4o-mini-tts, ≤ 4096 chars, mp3) | API | ok |
| tts | `elevenlabs` | | `ELEVENLABS_API_KEY` (eleven_flash_v2_5, opus_48000_64) | API | ok |
| tts | `piper` | | stub only — must run as a subprocess | GPL-3.0 | **gpl-isolated** |
| tts | `fake` | | — (440 Hz tone, len(text)/15 s, text in `ICMT`) | MIT | ok |
| vad | `fake` | yes | — (RMS > `voice_speaking_rms` per 30 ms frame) | MIT | ok |
| vad | `silero` | | `pip install silero-vad onnxruntime` (pulls torch) | MIT | ok |
| wake | `fake` | yes | — (phrase in the fake transcript) | MIT | ok |
| wake | `sherpa_onnx` | | `pip install sherpa-onnx` + KWS model folder | Apache-2.0 | ok |
| wake | `openwakeword` | | stub only — NC weights need an explicit opt-in | Apache code / NC weights | **nc-opt-in** |

The fakes are the default only for `vad` and `wake`, where no local default is installable
without heavy dependencies; for `stt`/`tts` the real local backend is the default and
`check()` says what is missing. The local libraries are **not** declared as package extras
(silero-vad pulls torch; KittenTTS ships as a GitHub wheel), so install them yourself:

```bash
uv pip install faster-whisper            # stt/faster_whisper
uv pip install silero-vad onnxruntime    # vad/silero
uv pip install sherpa-onnx               # wake/sherpa_onnx
uv pip install https://github.com/KittenML/KittenTTS/releases/download/<ver>/kittentts-<ver>-py3-none-any.whl
```

`ffmpeg` must be on `PATH` for anything that is not already a 16 kHz mono WAV.

## Picking backends (`~/.auoreo/settings.json`)

```json
{
  "backends": {"stt": "groq", "tts": "kittentts", "vad": "silero", "wake": "fake"},
  "voice_download_models": true,
  "voice_language": "en",
  "voice_tts_voice": "Jasper",
  "voice_stt_model": "whisper-large-v3",
  "voice_endpoint_silence_ms": 700,
  "voice_speaking_rms": 0.02
}
```

Keys live only in the secret store: `auoreo secrets set GROQ_API_KEY` (env → keyring →
`secrets.db`). They are sent as request headers and never appear in errors or events.
`voice_enabled: false` turns the whole service off (`/api/voice/*` answer 503).

## Push-to-talk API

```bash
# transcribe (multipart `file`; optional `language`, `session_id` as form fields or query)
curl -F file=@note.webm "http://127.0.0.1:4096/api/voice/transcribe?session_id=ses_…"
# → {"text": "...", "segments": [{start, end, text}], "language": "en",
#    "duration_s": 3.2, "backend": "faster_whisper", "run_id": "run_…"}

# speak (JSON; `format` wav|mp3|opus transcodes the backend output with ffmpeg)
curl -X POST localhost:4096/api/voice/speak -H 'content-type: application/json' \
     -d '{"text": "Hello there", "voice": null, "speed": 1.0}' -o reply.wav

curl localhost:4096/api/voice/status
```

Errors: 413 (> 25 MB), 422 (`no speech detected`, undecodable audio, nothing to speak),
503 (voice disabled, or the selected backend is not ready — the message carries the remedy),
404 (unknown `session_id`). With `session_id`, the transcript is submitted to the session as
a followup and `run_id` is returned; watch it on `/api/events` or `/ws`.

Events: `voice.transcribed{session_id, text, duration_s, backend}`,
`voice.spoke{chars, backend}`, and from WebRTC `voice.transcript{rtc_id, session_id, text,
duration_s}`, `voice.barge_in{rtc_id, session_id, cancelled, interrupted_turn?}`,
`voice.speaking{rtc_id, session_id, chars, truncated}`, `voice.spoke{rtc_id, session_id, chars,
duration_s, backend, cancelled}`, `voice.speech_cancelled{rtc_id, session_id, reason,
dropped_ms}`, `voice.error{rtc_id, session_id, message}`.
They go to the session aggregate when a session is known, else to `server`.

## Live audio (WebRTC)

When a peer is opened with `session_id`, each finished utterance is submitted to that session:
as `steer` while a turn is running, else as a `followup`. Endpointing uses the selected VAD
backend: speech confirmed after 100 ms, 300 ms pre-roll, utterance ends after
`voice_endpoint_silence_ms` of silence (200 ms of it kept) or at 30 s.

### The agent speaks back (F1)

Each peer whose offer carries an audio m-line (`sendrecv`, or `recvonly` to only listen) gets
an outbound audio track when the offer is answered (`auoreo.rtc.outbound`: 48 kHz mono s16,
20 ms frames, paced against a wall-clock origin, silence when idle). When a final assistant
`message.completed` (text, no tool calls) lands in the peer's session, `auoreo.voice.speaker`:

1. shapes it with `prepare_spoken_text` (no markdown, code or emoji);
2. caps it at `voice_speak_max_chars` (default 1500): cut at the last sentence end (else word)
   with "…" — no summary; the full answer stays in the transcript;
3. synthesizes sentence chunks (≤ 240 chars) one by one with the selected TTS backend,
   normalizes each to 16 kHz and queues it with `Peer.say` — the first sentence plays while
   the rest is synthesized;
4. emits `voice.speaking` before and `voice.spoke` after.

Opt out globally with `voice_speak_over_rtc: false`, per peer with `"speak": false` in the
`POST /api/rtc/offer` body. A peer without an audio m-line never speaks.

### Barge-in

While the agent is speaking, the normal 100 ms speech start is suspended and a Hermes-style
detector runs instead: the user barges in when ≥ 80 % of the last 300 ms is above the speech
threshold, and never in the first 500 ms after the speech started (the agent's own onset).
On a barge-in:

- `Peer.stop_speaking()` drops the queued audio immediately (`voice.speech_cancelled`);
- `voice.barge_in{cancelled: true}` is emitted and the utterance starts with 1200 ms of
  pre-roll, so the first words are not lost;
- the running turn is cancelled only with `voice_barge_in_interrupts_turn: true`; by default
  the utterance becomes a `steer` and only the spoken output is cut;
- the note `[the user interrupted your spoken reply]` is queued once for the session's next
  user text (TTL 120 s): as a `pending_user_messages` entry that the runner flushes after the
  current tool round, or — if still pending — appended to the next transcribed utterance.

Speech that starts while a turn runs but nothing is being spoken still emits
`voice.barge_in{cancelled: false}` and becomes a steer.

### Echo guard

Without headphones the agent hears itself. While outbound audio plays and for 300 ms after,
the speech threshold is `voice_speaking_rms × voice_echo_margin` (default ×2). Every inbound
frame is also compared with the last 1 s of what the outbound track actually played (16 kHz
ring buffer, normalised cross-correlation over all lags via FFT): above `voice_echo_corr`
(default 0.8) the frame is replaced by silence. A loopback test whose client mirrors the
agent's audio back produces no transcript and no barge-in.

### Still hardware-only

Tested only with aiortc loopback, synthetic tones and the fake TTS/VAD/STT backends. Not
exercised here: a real microphone/speaker, acoustic echo through a room (reverb and speaker
non-linearity lower the correlation; browsers' `echoCancellation: true` remains the first
line of defence), real speech against the energy VAD, and Opus over lossy networks.

## Meeting mode (F4)

Long-form recordings become a diarized, speaker-labelled transcript; a summarizer agent turns
it into decisions and action items. Meetings are **listen-only**: they never submit turns, so
the agent never speaks unless the meeting was started with `speak: true` *and* a WebRTC peer
is attached (then each utterance also goes to the peer's session and the F1 speaker answers).

```bash
curl -X POST localhost:4096/api/voice/meetings -H 'content-type: application/json' \
     -d '{"title": "Weekly sync", "session_id": null, "language": "en"}'   # → {"meeting_id": "mtg_…"}
curl -F file=@part1.webm localhost:4096/api/voice/meetings/mtg_…/audio          # append (repeat)
curl -X POST localhost:4096/api/voice/meetings/mtg_…/stop                       # finalise
curl localhost:4096/api/voice/meetings/mtg_…       # status + transcript [{start, end, speaker, text, confidence, tone}]
curl localhost:4096/api/voice/meetings             # list, newest first
curl -X POST localhost:4096/api/voice/meetings/mtg_…/summarize                  # → {run_id, session_id}
```

Finalise: the normalized chunks (16 kHz mono) are concatenated → VAD gate → the selected
`diarize` backend → STT on each speaker turn → `core.redaction` on every text → a tone label
per utterance. Stored under `~/.auoreo/meetings/<id>/` (`meeting.json`, `transcript.md`,
`summary.md`); audio chunks are deleted afterwards unless `voice_meeting_keep_audio: true`.
With `voice_meeting_ingest` (default true) the redacted markdown is ingested into memory with
provenance `meeting:<id>` (searchable with `knowledge_search`). Errors: 404 unknown meeting,
409 wrong state (e.g. audio after stop), 413/422/503 as for push-to-talk.

**Live mode:** `POST /api/voice/meetings/{id}/attach {"rtc_id": "…"}` (or `rtc_id` at start):
utterances endpointed by `rtc_bridge` for that peer are appended as chunks instead of being
submitted as turns; `stop` detaches the peer.

**Summary:** `/summarize` creates a session with agent `meeting_summarizer` and workflow
`meeting-summary` (tools `knowledge_search`, `read`, `create_goal`, `suggest`): sections
Summary, Decisions, Action items (owner = speaker label), Open questions; it may propose up
to three follow-ups (preferring consent-first `suggest`). When the run ends the text is saved
as `summary` and `meeting.summarised` is emitted.

Events: `meeting.started{meeting_id, title, live}`, `meeting.chunk{meeting_id, index,
duration_s, total_s, live}`, `meeting.finalised{meeting_id, status, segments, speakers}`,
`meeting.summarised{meeting_id, run_id, session_id, status}` — on the meeting's session when
given, else `server`. The UI lives at **Meetings** (`/meetings`).

### Diarization backends (`backends/diarize/`, spec §22-5)

| name | default | notes |
|---|---|---|
| `fake` | yes | Deterministic heuristic: energy gaps (≥ 0.3 s) split segments; loudness + zero-crossing-rate profiles assign `SPEAKER_00…`. Works for test tones, not real voices. |
| `sherpa_onnx` | | Local CPU default for real use: sherpa-onnx `OfflineSpeakerDiarization` (pyannote segmentation-3.0 ONNX + 3D-Speaker embedding + fast clustering). Paths `voice_diarize_segmentation_model` / `voice_diarize_embedding_model`; with `voice_download_models: true` the release assets are fetched into `~/.auoreo/models/diarize/`. `voice_diarize_num_speakers` (known count) or `voice_diarize_threshold`. |
| `pyannote` | | GPU meeting path: `pyannote/speaker-diarization-community-1` (gated, CC-BY-4.0). Accept the model conditions on Hugging Face, then `auoreo secrets set HF_TOKEN`; CUDA recommended. Uses `exclusive_speaker_diarization` (no overlapping turns). Pair with `stt/faster_whisper` for a WhisperX-style result. |

Select with `"backends": {"diarize": "sherpa_onnx"}`. Overlapping speech is attributed to one
speaker per turn; the transcript is a best effort, not a verbatim record.

## Tone and translation (F4)

**Tone (P1).** Each turn the runner classifies the user's text (`auoreo.voice.tone`:
multilingual-light lexicons, punctuation, ALL-CAPS/stretched words, emoji; deterministic) into
`neutral | frustrated | urgent | confused | positive` with a confidence, rendered in the
per-turn context block as `tone: frustrated (0.85)` (`{{tone}}` in `prompts/runtime.md`;
never the stable system prompt, so the prompt cache is unaffected). `tone_enabled: false`
turns it off. Meeting utterances also get a tone label, with prosody (RMS variance, words/s).

**Affect-adaptive policy (P2, opt-in).** With `tone_adaptive: true`, a frustrated or confused
tone at confidence ≥ 0.7 adds one line to the context block:
`guidance: be concise, acknowledge the issue, give the next concrete step`.

**Locale.** When `locale` is set and a cheap detector (script + stopwords) finds the user
writing in another language, the context block adds
`language: the user wrote in en; answer in de-DE (the configured locale)`.

**Translation.** Tool `translate {text, target, source?}` (deferred; BCP-47 tags) makes one
aux call (`purpose=translate`, temperature 0, the `small` group when configured) and returns
only the translation. `POST /api/voice/transcribe` takes `translate_to` (form or query) and
then also returns `translation`.

## Tools

- `transcribe {path, language?}` — `read` permission on the path (secret files stay blocked
  by the floor), allowed by default, deferred (find it with `tool_search`).
- `speak {text, voice?}` — `speak` permission, allowed by default, deferred; writes
  `~/.auoreo/sessions/<id>/audio/<n>.wav` (or `.mp3`/`.ogg`) and returns the path.

## The media sidecar (`auoreo voice-host`, F6)

Heavy or licence-encumbered engines can run out of the server process. Select the sidecar
backends and name what the sidecar itself should load:

```json
{
  "backends": {"stt": "sidecar", "tts": "sidecar", "vad": "sidecar"},
  "voice_host_backends": {"stt": "faster_whisper", "tts": "piper", "vad": "silero"},
  "voice_host_python": "/opt/auoreo-gpu/.venv/bin/python",
  "voice_host_timeout_s": 60
}
```

```
server process                                   sidecar process (own venv, optional GPU)
VoiceService ─ stt/tts/vad "sidecar" backends ─┐  python -m auoreo.voice.host
                                               └─ VoiceHost ══ JSON-RPC 2.0 over stdio ══▶ inner
                                                  (auoreo.voice.sidecar)                  backends
```

- **One shared process** for the stt/tts/vad trio (`auoreo.voice.sidecar.VoiceHost`), started
  lazily on the first call. Wire format is the plugin host's `RpcPeer` framing (one JSON object
  per line): `check`, `transcribe {wav_b64, language, keyterms}`, `synthesize {text, voice,
  speed}`, `vad {wav_b64}`, `shutdown`. Audio travels base64, capped at 25 MB decoded in both
  directions (checked before sending and again in the sidecar).
- **Crash isolation**: a segfault in a model kills only the sidecar. The failing request errors,
  the next call restarts it with backoff (0 / 0.25 / 0.5 / 1 s), at most 3 restarts per 60 s;
  after that every call and `check()` (doctor, `/api/voice/status`) fails honestly with the
  last stderr lines until the crashes age out. A request that exceeds `voice_host_timeout_s`
  fails alone; the process keeps running. The sidecar shuts down with the server
  (`Services.close_backends` → each sidecar backend's `close()`).
- **Scrubbed env**: the child gets `core.secrets.child_env` (PATH, HOME, LANG, …) plus only the
  secrets the *inner* backends declare in `requires_env`, `AUOREO_HOME`, and a JSON snapshot of
  the non-secret `voice_*` / `backends` settings. Provider keys such as `OPENROUTER_API_KEY`
  never reach it. The sidecar's real stdout is reserved for the protocol (library prints go to
  stderr).
- **Licence isolation**: piper (GPL-3.0) and openwakeword (model weights CC BY-NC-SA) are never
  imported by the server; with `voice_host_backends` they load only in the sidecar's
  interpreter, which can be a separate venv you install them into.
- **Live audio**: per-frame `is_speech` (20–30 ms RTC frames, synchronous) stays local as an
  energy test against `voice_speaking_rms`; the pre-STT silence gate (`speech_segments`),
  transcription and synthesis go through the sidecar. Wake word is not proxied yet.

### Pointing it at a GPU venv

```bash
uv venv /opt/auoreo-gpu/.venv
uv pip install --python /opt/auoreo-gpu/.venv/bin/python auoreo==<server version> faster-whisper silero-vad torch
auoreo config set voice_host_python /opt/auoreo-gpu/.venv/bin/python
auoreo voice-host --check     # starts the sidecar, prints `voice-host pid N (stt=…, tts=…, vad=…)`
```

The sidecar imports `auoreo` itself (for the backend ABCs and discovery), so its venv needs the
same auoreo version. `voice_host_backends.*` cannot be `sidecar` (refused: it would recurse).
A PyInstaller build has no `-m`; point `voice_host_python` at a real interpreter there.
