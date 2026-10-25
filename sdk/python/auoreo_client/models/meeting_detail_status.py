from enum import StrEnum


class MeetingDetailStatus(StrEnum):
    FAILED = "failed"
    FINALISED = "finalised"
    FINALISING = "finalising"
    RECORDING = "recording"

    def __str__(self) -> str:
        return str(self.value)
