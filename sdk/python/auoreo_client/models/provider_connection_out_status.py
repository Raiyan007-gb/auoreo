from enum import StrEnum


class ProviderConnectionOutStatus(StrEnum):
    ERROR = "error"
    INVALID = "invalid"
    OK = "ok"
    UNCHECKED = "unchecked"

    def __str__(self) -> str:
        return str(self.value)
