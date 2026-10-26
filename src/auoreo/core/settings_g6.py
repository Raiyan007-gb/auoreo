"""G6: voice lanes (OpenAI Realtime WebRTC, streaming STT, porcupine, edge-tts, more cloud STT),
preference proposals, the skills hub and isolated review worktrees.

Attached to `Settings` as `g6: G6Settings`; set it in `settings.json`
(`{"g6": {"skills": {"taps": ["https://example.com/index.json"]}}}`) or as a JSON env var
(`AUOREO_G6='{...}'`). Secrets never live here: API keys come from the secret store.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RealtimeSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = "gpt-realtime"
    """Realtime model the ephemeral client secret is minted for."""
    voice: str = "marin"
    base_url: str = "https://api.openai.com/v1"
    """`<base>/realtime/client_secrets` (mint) and `<base>/realtime/calls` (SDP exchange)."""
    transcription_model: str | None = "gpt-4o-transcribe"
    """Input-audio transcription inside the realtime session (None = off)."""


class VoiceG6Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    realtime: RealtimeSettings = Field(default_factory=RealtimeSettings)
    deepgram_url: str = "wss://api.deepgram.com/v1/listen"
    deepgram_model: str = "nova-3"
    mistral_model: str = "voxtral-mini-latest"
    elevenlabs_stt_model: str = "scribe_v2"
    deepinfra_model: str = "openai/whisper-large-v3-turbo"
    deepinfra_base_url: str = "https://api.deepinfra.com/v1/openai"
    xai_experimental: bool = False
    """xAI STT is experimental (endpoint not documented as stable): off unless set."""
    xai_stt_url: str = "https://api.x.ai/v1/stt"
    porcupine_keywords: list[str] = Field(default_factory=lambda: ["porcupine"])
    """Built-in porcupine keywords (`pvporcupine.KEYWORDS`); ignored when keyword paths are set."""
    porcupine_keyword_paths: list[str] = Field(default_factory=list)
    """Custom `.ppn` files (never committed to the repo)."""
    porcupine_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    """Higher = more sensitive (the opposite polarity of the sherpa threshold)."""
    edge_tts_command: list[str] = Field(default_factory=lambda: ["edge-tts"])
    """How to run edge-tts out of process, e.g. `["/opt/voice/.venv/bin/python", "-m", "edge_tts"]`."""
    edge_tts_voice: str = "en-US-EmmaMultilingualNeural"
    sherpa_face_model_dir: str | None = None


class SkillsHubSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    taps: list[str] = Field(default_factory=list)
    """Hub sources: an HTTP(S) JSON index URL or a git URL (`git+https://…` or `….git`)."""
    index_ttl_s: int = Field(default=3600, ge=0)
    """How long a fetched tap index is reused before refetching (1 h)."""
    max_skill_bytes: int = Field(default=1_048_576, ge=1024)
    """Size cap for one downloaded skill (all files)."""


class PrefsSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_pending: int = Field(default=50, ge=1, le=1000)
    """Pending PREFS.md proposals kept at most; older pending ones expire as `denied`."""


class ReviewSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    worktree_root: str | None = None
    """Where isolated review worktrees go; default `<cwd>/.auoreo/worktrees` (git-ignored)."""


class G6Settings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    voice: VoiceG6Settings = Field(default_factory=VoiceG6Settings)
    skills: SkillsHubSettings = Field(default_factory=SkillsHubSettings)
    prefs: PrefsSettings = Field(default_factory=PrefsSettings)
    review: ReviewSettings = Field(default_factory=ReviewSettings)
