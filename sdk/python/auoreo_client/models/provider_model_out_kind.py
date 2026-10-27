from enum import StrEnum


class ProviderModelOutKind(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    OTHER = "other"

    def __str__(self) -> str:
        return str(self.value)
