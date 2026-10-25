from enum import StrEnum


class DoctorCheckLevel(StrEnum):
    ERROR = "error"
    OK = "ok"
    WARN = "warn"

    def __str__(self) -> str:
        return str(self.value)
