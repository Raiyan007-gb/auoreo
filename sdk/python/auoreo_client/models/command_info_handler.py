from enum import StrEnum


class CommandInfoHandler(StrEnum):
    COMPACT = "compact"
    MODEL = "model"
    PLAN = "plan"
    PROMPT = "prompt"

    def __str__(self) -> str:
        return str(self.value)
