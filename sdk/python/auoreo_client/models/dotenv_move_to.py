from enum import StrEnum


class DotenvMoveTo(StrEnum):
    KEPT = "kept"
    SECRETS = "secrets"
    SETTINGS = "settings"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        return str(self.value)
