from enum import StrEnum


class HubRemovedStatus(StrEnum):
    APPROVED = "approved"
    QUARANTINED = "quarantined"

    def __str__(self) -> str:
        return str(self.value)
