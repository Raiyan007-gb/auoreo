from enum import StrEnum


class DoctorFixKind(StrEnum):
    CHMOD = "chmod"
    MKDIR = "mkdir"
    WORKTREE = "worktree"

    def __str__(self) -> str:
        return str(self.value)
