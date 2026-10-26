from enum import StrEnum


class HubInstalledSkillIntegrity(StrEnum):
    MISSING = "missing"
    MODIFIED = "modified"
    OK = "ok"

    def __str__(self) -> str:
        return str(self.value)
