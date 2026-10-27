from enum import StrEnum


class EnvEntrySource(StrEnum):
    DB = "db"
    DEFAULT = "default"
    ENV = "env"
    OVERRIDE = "override"
    SECRETS = "secrets"

    def __str__(self) -> str:
        return str(self.value)
