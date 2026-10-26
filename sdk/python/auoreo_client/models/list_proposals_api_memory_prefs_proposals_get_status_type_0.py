from enum import StrEnum


class ListProposalsApiMemoryPrefsProposalsGetStatusType0(StrEnum):
    APPROVED = "approved"
    DENIED = "denied"
    PENDING = "pending"

    def __str__(self) -> str:
        return str(self.value)
