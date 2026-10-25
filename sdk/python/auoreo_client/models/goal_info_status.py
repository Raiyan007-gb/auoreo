from enum import StrEnum


class GoalInfoStatus(StrEnum):
    ABSORBED = "absorbed"
    ACTIVE = "active"
    DONE = "done"
    DROPPED = "dropped"
    PAUSED = "paused"

    def __str__(self) -> str:
        return str(self.value)
