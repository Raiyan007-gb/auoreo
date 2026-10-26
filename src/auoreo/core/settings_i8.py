"""I8: voice closure (wake daemon, streaming STT into RTC, hallucination/echo filter, STT
gates + keyterms + local_command + model auto-correct, Mistral/NeuTTS TTS, barge-in noise
floor, cloud upload shaping, WS Realtime fallback), the LiveKit worker / token / webhook,
computer-use safety, web computer use (Playwright trace, Chrome CDP) and vision details
(spec §13, §14, §24.5, §24.6, §25.1–§25.5, §26.6–§26.10).

Attached to `Settings` as `i8: I8Settings`; set it in `settings.json`
(`{"i8": {"wake": {"enabled": true}}}`) or as a JSON env var (`AUOREO_I8='{...}'`).
Secrets never live here: API keys come from the secret store.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Cfg(BaseModel):
    model_config = ConfigDict(extra="ignore")


class WakeRoute(_Cfg):
    """Where a wake phrase sends the following utterance (spec §24.5 multi-profile routing)."""

    agent: str | None = None
    """Agent folder for a new session (None = the default agent)."""
    session_id: str | None = None
    """Continue this session instead of creating one."""
    title: str = "voice"


class WakeSettings(_Cfg):
    enabled: bool = False
    """Run the wake daemon (opt-in: it opens the microphone). Continuous listening only
    happens for one utterance after a wake hit; push-to-talk stays the default."""
    host_backend: str = "fake"
    """Wake backend the voice media sidecar loads for `backends.wake = "sidecar"` (its `wake`
    RPC method); never `sidecar` itself."""
    window_s: float = Field(default=1.5, ge=0.5, le=5.0)
    """Rolling audio window handed to `detect()`."""
    hop_s: float = Field(default=0.5, ge=0.1, le=2.0)
    """How often the window is checked."""
    cooldown_s: float = Field(default=2.0, ge=0.0, le=30.0)
    dead_mic_peak: int = Field(default=10, ge=0, le=32767)
    """Peak |int16| at or below this for `dead_mic_window_s` → status `dead_mic`."""
    dead_mic_window_s: float = Field(default=10.0, ge=1.0, le=120.0)
    listen_timeout_s: float = Field(default=8.0, ge=1.0, le=60.0)
    """After a hit: give up when no speech starts within this long."""
    max_utterance_s: float = Field(default=15.0, ge=1.0, le=60.0)
    routes: dict[str, WakeRoute] = Field(default_factory=dict)
    """Wake phrase (lower-case) → route; an unknown phrase uses the default route."""
    lock_name: str = "wake.lock"


class LocalCommandSettings(_Cfg):
    template: list[str] = Field(default_factory=list)
    """argv with `{input}`, `{model}`, `{output_dir}` placeholders, e.g.
    `["whisper-cli", "{input}", "--model", "{model}", "--output_format", "txt"]` (no shell)."""
    model: str = "base"
    timeout_s: float = Field(default=30.0, gt=0, le=600)
    allowlist: list[str] = Field(default_factory=list)
    """Executable basenames allowed to run; empty = deny (spec §25.1)."""


class SttI8Settings(_Cfg):
    streaming_rtc: bool = True
    """Use `SttBackend.stream()` for live RTC utterances when the backend streams."""
    filter_enabled: bool = True
    blocklist: list[str] = Field(
        default_factory=lambda: [
            "thank you for watching",
            "thanks for watching",
            "please subscribe",
            "subtitles by the amara.org community",
            "transcribed by",
            "you",
            "bye",
            "thank you",
        ]
    )
    """Whole-utterance Whisper hallucinations (compared after lower-casing and stripping punctuation)."""
    repeat_min: int = Field(default=4, ge=2, le=20)
    """A word or short phrase repeated this many times in a row is a hallucination loop."""
    echo_similarity: float = Field(default=0.6, ge=0.0, le=1.0)
    """Drop a transcript this similar to something the agent just said (TTS echo)."""
    echo_window_s: float = Field(default=20.0, ge=1.0, le=300.0)
    no_speech_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    logprob_threshold: float = Field(default=-1.0, le=0.0)
    device: Literal["auto", "cpu", "cuda"] = "auto"
    """faster-whisper device; `auto` tries CUDA and falls back to CPU."""
    unload_after_idle_s: float = Field(default=300.0, ge=0.0)
    """Unload the faster-whisper model after this long unused (0 = never)."""
    keyterms: bool = True
    """Bias STT with project basename + git branch + recent file names (≤ `keyterms_max`)."""
    keyterms_max: int = Field(default=50, ge=0, le=200)
    local_command: LocalCommandSettings = Field(default_factory=LocalCommandSettings)
    trim_silence: bool = True
    """Trim leading/trailing silence before a cloud upload."""
    cloud_aac: bool = False
    """Encode cloud uploads as 16 kHz mono AAC (`audio.m4a`, fragmented MP4) when ffmpeg exists."""


class TtsI8Settings(_Cfg):
    mistral_model: str = "voxtral-mini-tts-2603"
    mistral_voice: str | None = None
    mistral_url: str = "https://api.mistral.ai/v1/audio/speech"
    neutts_model: str = "neuphonic/neutts-air"
    neutts_codec: str = "neuphonic/neucodec"
    neutts_nc_opt_in: bool = False
    """Allow NeuTTS variants other than Air (NeuTTS Open License, NC-like)."""
    neutts_ref_audio: str | None = None
    neutts_ref_text: str | None = None


class BargeI8Settings(_Cfg):
    calibrate_ms: int = Field(default=400, ge=0, le=5000)
    """Inbound audio used to measure the per-peer noise floor."""
    sustain_ms: int = Field(default=300, ge=50, le=2000)
    """Window over which ≥ 80 % of frames must be loud to barge in."""
    floor_multiplier: float = Field(default=3.0, ge=1.0, le=50.0)
    """A barge-in frame must be this many times louder than the noise floor."""


class RealtimeWsSettings(_Cfg):
    enabled: bool = True
    """Offer the WebSocket Realtime endpoint as a fallback in `openai_realtime` joins."""
    url: str = "wss://api.openai.com/v1/realtime"


class LiveKitWorkerSettings(_Cfg):
    enabled: bool = False
    """Run a `livekit-agents` AgentServer worker in-process (needs `livekit-agents` installed
    and `backends.rtc_transport = "livekit"`)."""
    agent_name: str = "auoreo"
    """Explicit-dispatch name (`roomConfig.agents[].agent_name`)."""
    default_agent: str | None = None
    """AUOREO agent for sessions the worker creates for rooms it was not told about."""


class LiveKitI8Settings(_Cfg):
    worker: LiveKitWorkerSettings = Field(default_factory=LiveKitWorkerSettings)
    webhook_enabled: bool = True
    webhook_rate_per_min: int = Field(default=30, ge=1, le=10_000)
    webhook_dedupe_s: float = Field(default=3600.0, ge=60.0)
    max_metadata_bytes: int = Field(default=1024, ge=0, le=16_384)
    web_toggle: bool = True
    """Report LiveKit availability to the SPA call panel (it shows the transport toggle)."""


class ComputerUseI8Settings(_Cfg):
    mode: Literal["som", "vision", "ax"] = "som"
    max_image_dimension: int = Field(default=1456, ge=256, le=4096)
    no_overlay: bool | None = None
    """None = per-OS default (off on macOS/X11/headless/WSL2, on on Windows/Wayland)."""
    move_settle_ms: int = Field(default=50, ge=0, le=1000)
    type_via_clipboard_chars: int = Field(default=200, ge=0)
    """`type` of at least this many (or non-ASCII) chars pastes via the clipboard (0 = never)."""
    input_target_guard: bool = True
    unhide_on_turn_end: bool = True


class BrowserI8Settings(_Cfg):
    trace: bool = False
    """Record a Playwright trace per browser session under `<home>/sessions/<id>/`."""
    cdp_url: str = "http://127.0.0.1:9222"
    """DevTools endpoint of a Chrome you started yourself with `--remote-debugging-port`."""


class VisionI8Settings(_Cfg):
    native_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    aux_timeout_s: float = Field(default=120.0, gt=0, le=600)
    min_edge: int = Field(default=64, ge=16, le=512)
    provider_max_bytes: int = 5 * 1024 * 1024
    provider_max_px: int = 8000


class I8Settings(_Cfg):
    wake: WakeSettings = Field(default_factory=WakeSettings)
    stt: SttI8Settings = Field(default_factory=SttI8Settings)
    tts: TtsI8Settings = Field(default_factory=TtsI8Settings)
    barge: BargeI8Settings = Field(default_factory=BargeI8Settings)
    realtime_ws: RealtimeWsSettings = Field(default_factory=RealtimeWsSettings)
    livekit: LiveKitI8Settings = Field(default_factory=LiveKitI8Settings)
    computer_use: ComputerUseI8Settings = Field(default_factory=ComputerUseI8Settings)
    browser: BrowserI8Settings = Field(default_factory=BrowserI8Settings)
    vision: VisionI8Settings = Field(default_factory=VisionI8Settings)


def i8_of(obj: object) -> I8Settings:
    """`settings.i8` from Settings, Services or a ToolContext (defaults when absent)."""
    for cand in (obj, getattr(obj, "settings", None)):
        i8 = getattr(cand, "i8", None)
        if isinstance(i8, I8Settings):
            return i8
    services = getattr(obj, "services", None)
    if isinstance(services, dict):
        svc = services.get("services")
        i8 = getattr(getattr(svc, "settings", None), "i8", None)
        if isinstance(i8, I8Settings):
            return i8
    return I8Settings()
