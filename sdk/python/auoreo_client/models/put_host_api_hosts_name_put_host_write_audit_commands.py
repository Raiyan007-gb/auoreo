from enum import StrEnum


class PutHostApiHostsNamePutHostWriteAuditCommands(StrEnum):
    HASH = "hash"
    REDACTED = "redacted"

    def __str__(self) -> str:
        return str(self.value)
