from enum import StrEnum


class ChatRequestMode(StrEnum):
    FOLLOWUP = "followup"
    INJECT = "inject"
    STEER = "steer"

    def __str__(self) -> str:
        return str(self.value)
