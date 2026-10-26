from enum import StrEnum


class PluginInfoStatus(StrEnum):
    CRASHED = "crashed"
    DISABLED = "disabled"
    INVALID_CONFIG = "invalid_config"
    QUARANTINED = "quarantined"
    RUNNING = "running"
    STOPPED = "stopped"
    UNVERIFIED = "unverified"

    def __str__(self) -> str:
        return str(self.value)
