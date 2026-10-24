"""Model resolution chain (spec §10.2) and gateway factory."""

from __future__ import annotations

from collections.abc import Callable

from auoreo.core.settings import Settings
from auoreo.gateway.types import Gateway

BUILTIN_DEFAULT = "default"


def resolve_model(
    settings: Settings,
    *,
    session_override: str | None = None,
    flag: str | None = None,
    agent_model: str | None = None,
) -> str:
    """First hit wins: session override → explicit flag → agent manifest → AUOREO_MODEL
    → `default` routing group. Aliases from settings are expanded once."""
    raw = session_override or flag or agent_model or settings.model or BUILTIN_DEFAULT
    return settings.aliases.get(raw, raw)


def make_gateway(settings: Settings, secret: Callable[[str], str | None]) -> Gateway:
    """Dict-dispatched factory; `litellm` is only imported when actually selected."""

    def fake() -> Gateway:
        from auoreo.gateway.fake import FakeGateway

        return FakeGateway()

    def real() -> Gateway:
        from auoreo.gateway.gateway import LiteLLMGateway

        return LiteLLMGateway(settings, secret)

    factories: dict[str, Callable[[], Gateway]] = {"fake": fake, "litellm": real}
    return factories[settings.gateway]()
