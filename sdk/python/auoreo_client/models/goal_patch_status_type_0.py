from enum import StrEnum


class GoalPatchStatusType0(StrEnum):
    ACTIVE = "active"
    DONE = "done"
    DROPPED = "dropped"
    PAUSED = "paused"

    def __str__(self) -> str:
        return str(self.value)
