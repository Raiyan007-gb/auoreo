from enum import StrEnum


class ReloadApiHarnessReloadPostKindType0(StrEnum):
    AGENT = "agent"
    COMMAND = "command"
    CONFIG = "config"
    LSP = "lsp"
    MCP = "mcp"
    MODEL = "model"

    def __str__(self) -> str:
        return str(self.value)
