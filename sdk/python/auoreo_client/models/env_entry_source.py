from enum import StrEnum


class EnvEntrySource(StrEnum):
    DEFAULT = "default"
    DOTENV = "dotenv"
    ENV = "env"
    OVERRIDE = "override"
    SECRETS = "secrets"
    SETTINGS = "settings"

    def __str__(self) -> str:
        return str(self.value)
