from enum import StrEnum


class SessionSourceKind(StrEnum):
    MCP = "mcp"
    PLUGIN = "plugin"

    def __str__(self) -> str:
        return str(self.value)
