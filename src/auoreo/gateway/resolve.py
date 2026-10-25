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
    return expand_alias(settings, raw)


# Built-in Anthropic aliases (spec §10.2), resolved through whichever provider the `default`
# group uses so `opus` works with an OpenRouter-only or Anthropic-only key. User `aliases`
# and routing-group names always win. `opusplan` = Opus (plan-mode model swap).
ANTHROPIC_ALIASES: dict[str, str] = {
    "opus": "claude-opus-5-5",
    "sonnet": "claude-sonnet-5-5",
    "haiku": "claude-haiku-5-5",
    "opusplan": "claude-opus-5-5",
}
_ALIAS_ROUTES: dict[str, Callable[[str], str]] = {
    "openrouter": lambda mid: f"openrouter/anthropic/{_dotted(mid)}",
    "anthropic": lambda mid: f"anthropic/{mid}",
    "bedrock": lambda mid: f"bedrock/anthropic.{mid}",
    "vertex_ai": lambda mid: f"vertex_ai/{mid}",
}


def _dotted(model_id: str) -> str:
    """OpenRouter spells versions with a dot: `claude-opus-5-5` → `claude-opus-5.5`."""
    head, _, tail = model_id.rpartition("-")
    return f"{head[:-2]}-{head[-1]}.{tail}" if head[-2:-1] == "-" and head[-1].isdigit() else model_id


def builtin_alias(settings: Settings, name: str) -> str | None:
    base = name.removesuffix("[1m]")
    mid = ANTHROPIC_ALIASES.get(base)
    if mid is None:
        return None
    default = settings.models.get("default")
    provider = default.models[0].split("/", 1)[0] if default and default.models else "openrouter"
    route = _ALIAS_ROUTES.get(provider, _ALIAS_ROUTES["openrouter"])
    return route(mid) + name[len(base) :]


def expand_alias(settings: Settings, name: str) -> str:
    """User alias → routing group as-is → built-in alias → unchanged."""
    if name in settings.aliases:
        return settings.aliases[name]
    if name in settings.models:
        return name
    return builtin_alias(settings, name) or name


def primary_deployment(settings: Settings, model: str) -> str:
    """The first deployment of a routing group (or the provider id itself)."""
    model = expand_alias(settings, model)
    group = settings.models.get(model)
    return group.models[0] if group and group.models else model


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
