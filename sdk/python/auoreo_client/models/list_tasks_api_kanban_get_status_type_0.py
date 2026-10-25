from enum import StrEnum


class ListTasksApiKanbanGetStatusType0(StrEnum):
    BLOCKED = "blocked"
    CLAIMED = "claimed"
    DONE = "done"
    READY = "ready"

    def __str__(self) -> str:
        return str(self.value)
