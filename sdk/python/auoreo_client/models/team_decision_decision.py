from enum import StrEnum


class TeamDecisionDecision(StrEnum):
    CONTINUE = "continue"
    FINISH = "finish"
    SPAWN = "spawn"

    def __str__(self) -> str:
        return str(self.value)
