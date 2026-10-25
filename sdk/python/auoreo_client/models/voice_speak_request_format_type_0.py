from enum import StrEnum


class VoiceSpeakRequestFormatType0(StrEnum):
    MP3 = "mp3"
    OPUS = "opus"
    WAV = "wav"

    def __str__(self) -> str:
        return str(self.value)
