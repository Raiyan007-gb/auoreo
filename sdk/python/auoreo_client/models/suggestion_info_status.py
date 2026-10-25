from enum import StrEnum


class SuggestionInfoStatus(StrEnum):
    ACCEPTED = "accepted"
    DENIED = "denied"
    EXPIRED = "expired"
    PENDING = "pending"
    TOMBSTONED = "tombstoned"

    def __str__(self) -> str:
        return str(self.value)
