from enum import StrEnum


class WelcomeEventStep(StrEnum):
    CREDENTIALS = "credentials"
    DEMO = "demo"
    DOCTOR = "doctor"
    MODEL = "model"
    THEME = "theme"
    TUNNEL = "tunnel"
    WIZARD = "wizard"

    def __str__(self) -> str:
        return str(self.value)
