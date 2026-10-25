from enum import StrEnum


class HookAckStatus(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"

    def __str__(self) -> str:
        return str(self.value)
