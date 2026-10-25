from enum import StrEnum


class ListGoalsApiGoalsGetStatusType0(StrEnum):
    ABSORBED = "absorbed"
    ACTIVE = "active"
    ALL = "all"
    DONE = "done"
    DROPPED = "dropped"
    PAUSED = "paused"

    def __str__(self) -> str:
        return str(self.value)
