from enum import StrEnum


class PrefsProposalAction(StrEnum):
    ADD = "add"
    REMOVE = "remove"
    REPLACE = "replace"

    def __str__(self) -> str:
        return str(self.value)
