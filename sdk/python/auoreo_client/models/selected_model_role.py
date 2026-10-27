from enum import StrEnum


class SelectedModelRole(StrEnum):
    EMBEDDING = "embedding"
    MAIN = "main"
    SMALL = "small"

    def __str__(self) -> str:
        return str(self.value)
