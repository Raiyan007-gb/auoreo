from enum import StrEnum


class ExportApiSessionsSessionIdExportGetFormat(StrEnum):
    JSON = "json"
    MD = "md"

    def __str__(self) -> str:
        return str(self.value)
