from enum import StrEnum


class WelcomeEventAction(StrEnum):
    COMPLETED = "completed"
    DISMISSED = "dismissed"
    FINISHED = "finished"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        return str(self.value)
