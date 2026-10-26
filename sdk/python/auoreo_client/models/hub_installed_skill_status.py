from enum import StrEnum


class HubInstalledSkillStatus(StrEnum):
    APPROVED = "approved"
    QUARANTINED = "quarantined"

    def __str__(self) -> str:
        return str(self.value)
