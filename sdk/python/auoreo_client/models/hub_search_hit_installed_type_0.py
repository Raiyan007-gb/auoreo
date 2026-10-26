from enum import StrEnum


class HubSearchHitInstalledType0(StrEnum):
    APPROVED = "approved"
    QUARANTINED = "quarantined"

    def __str__(self) -> str:
        return str(self.value)
