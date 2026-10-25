from enum import StrEnum


class EnvironmentInfoKind(StrEnum):
    BACKEND = "backend"
    BRIDGE = "bridge"
    NAMED = "named"
    REMOTE = "remote"

    def __str__(self) -> str:
        return str(self.value)
