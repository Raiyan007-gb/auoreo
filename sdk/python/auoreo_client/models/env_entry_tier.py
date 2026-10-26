from enum import StrEnum


class EnvEntryTier(StrEnum):
    ANYWHERE = "anywhere"
    ENV_ONLY = "env-only"
    HOME = "home"

    def __str__(self) -> str:
        return str(self.value)
