from enum import StrEnum


class WebhookRouteInfoProvider(StrEnum):
    GENERIC = "generic"
    GITHUB = "github"

    def __str__(self) -> str:
        return str(self.value)
