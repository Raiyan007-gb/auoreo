from enum import StrEnum


class PluginInfoVerify(StrEnum):
    MISMATCH = "mismatch"
    MISSING = "missing"
    OK = "ok"

    def __str__(self) -> str:
        return str(self.value)
