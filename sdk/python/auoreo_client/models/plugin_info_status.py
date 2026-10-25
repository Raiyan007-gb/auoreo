from enum import StrEnum


class PluginInfoStatus(StrEnum):
    CRASHED = "crashed"
    DISABLED = "disabled"
    INVALID_CONFIG = "invalid_config"
    RUNNING = "running"
    STOPPED = "stopped"

    def __str__(self) -> str:
        return str(self.value)
