from enum import StrEnum


class ListSuggestionsApiSuggestionsGetStatusType0(StrEnum):
    ACCEPTED = "accepted"
    ALL = "all"
    DENIED = "denied"
    EXPIRED = "expired"
    PENDING = "pending"
    TOMBSTONED = "tombstoned"

    def __str__(self) -> str:
        return str(self.value)
