from enum import StrEnum


class BanditStatusContextsMode(StrEnum):
    AGENT = "agent"
    AGENTSIZE = "agent+size"

    def __str__(self) -> str:
        return str(self.value)
