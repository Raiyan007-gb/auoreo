from enum import StrEnum


class PlanStepStatus(StrEnum):
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    PENDING = "pending"

    def __str__(self) -> str:
        return str(self.value)
