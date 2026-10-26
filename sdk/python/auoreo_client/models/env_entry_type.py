from enum import StrEnum


class EnvEntryType(StrEnum):
    BOOL = "bool"
    ENUM = "enum"
    FLOAT = "float"
    INT = "int"
    JSON = "json"
    STR = "str"

    def __str__(self) -> str:
        return str(self.value)
