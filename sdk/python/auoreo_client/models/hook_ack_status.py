from enum import StrEnum


class HookAckStatus(StrEnum):
    ACCEPTED = "accepted"
    DELIVERED = "delivered"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    IGNORED = "ignored"

    def __str__(self) -> str:
        return str(self.value)
