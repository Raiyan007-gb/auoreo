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
duration_s}`, `voice.barge_in{rtc_id, session_id}`, `voice.error{rtc_id, session_id, message}`.
They go to the session aggregate when a session is known, else to `server`.

## Live audio (WebRTC)

When a peer is opened with `session_id`, each finished utterance is submitted to that session:
as `steer` while a turn is running, else as a `followup`. Endpointing uses the selected VAD
backend: speech confirmed after 100 ms, 300 ms pre-roll, utterance ends after
`voice_endpoint_silence_ms` of silence (200 ms of it kept) or at 30 s.

**Barge-in:** speech starting while the session's turn runs emits `voice.barge_in`. There is
no outbound TTS track yet, so nothing is stopped; when the TTS → audio-track item lands, the
same signal pauses playback before the steer is injected (spec §13.5). Echo guarding
(`is_tts_echo`) belongs to that item too.

## Tools

- `transcribe {path, language?}` — `read` permission on the path (secret files stay blocked
  by the floor), allowed by default, deferred (find it with `tool_search`).
- `speak {text, voice?}` — `speak` permission, allowed by default, deferred; writes
  `~/.auoreo/sessions/<id>/audio/<n>.wav` (or `.mp3`/`.ogg`) and returns the path.

## Later: the media sidecar

The spec's end state runs capture, VAD, wake and local models in a separate
`auoreo-voice-host` process (stdio JSON-lines `Hello/Ready/Capture/Transcript/Speak/Stop`) so
audio libraries never load in the server. The backend ABCs are the seam: a sidecar backend
implements the same `transcribe`/`synthesize`/`speech_segments`/`detect` calls over that
protocol, and nothing above the ABC changes. Until then the local backends run in worker
threads of the server process, loaded only when selected.
