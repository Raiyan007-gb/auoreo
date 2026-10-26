from enum import StrEnum


class SecretWriteResultAction(StrEnum):
    ROTATE = "rotate"
    SET = "set"

    def __str__(self) -> str:
        return str(self.value)
