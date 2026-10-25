from enum import StrEnum


class BanditArmInfoEffortType0(StrEnum):
    HIGH = "high"
    LOW = "low"
    MAX = "max"
    MEDIUM = "medium"

    def __str__(self) -> str:
        return str(self.value)
