"""STT model-ID auto-correct dictionary (spec §25.1, no `elif`).

`voice_stt_model` is one setting shared by every STT lane, so a model id from another lane
(e.g. `whisper-1` while `backends.stt = "groq"`) would be sent to the wrong provider. The
lane tables below remap a cross-lane or unknown id to the lane default; `VoiceService`
emits `voice.stt_model_corrected` and audits `{requested, corrected, lane}` once per pair.
"""

from __future__ import annotations

from dataclasses import dataclass

GROQ_SET = frozenset({"whisper-large-v3-turbo", "whisper-large-v3"})
OAI_SET = frozenset(
    {
        "whisper-1",
        "gpt-4o-mini-transcribe",
        "gpt-4o-transcribe",
        "gpt-transcribe",
        "gpt-4o-transcribe-diarize",
    }
)
LANES: dict[str, tuple[frozenset[str] | None, str]] = {
    # lane: (accepted ids or None = any id, default)
    "groq": (GROQ_SET, "whisper-large-v3-turbo"),
    "openai": (OAI_SET, "gpt-4o-transcribe"),
    "faster_whisper": (
        frozenset(
            {"tiny", "tiny.en", "base", "base.en", "small", "small.en", "medium", "medium.en"}
            | {"large-v1", "large-v2", "large-v3", "large-v3-turbo", "turbo", "distil-large-v3"}
        ),
        "base",
    ),
    "local_command": (None, "base"),
}


@dataclass(frozen=True)
class Correction:
    lane: str
    requested: str
    corrected: str

    def as_dict(self) -> dict[str, str]:
        return {"lane": self.lane, "requested": self.requested, "corrected": self.corrected}


def correct(lane: str, requested: str | None) -> Correction | None:
    """The remap for `requested` on `lane`, or None when it is fine / the lane has no table."""
    if not requested or lane not in LANES:
        return None
    accepted, default = LANES[lane]
    if accepted is None or requested in accepted:
        return None
    return Correction(lane, requested, default)
