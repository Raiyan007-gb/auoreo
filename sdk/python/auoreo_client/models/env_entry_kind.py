from enum import StrEnum


class EnvEntryKind(StrEnum):
    PASSTHROUGH = "passthrough"
    SECRET = "secret"
    SETTING = "setting"

    def __str__(self) -> str:
        return str(self.value)
