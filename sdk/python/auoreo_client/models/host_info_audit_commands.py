from enum import StrEnum


class HostInfoAuditCommands(StrEnum):
    HASH = "hash"
    REDACTED = "redacted"

    def __str__(self) -> str:
        return str(self.value)
