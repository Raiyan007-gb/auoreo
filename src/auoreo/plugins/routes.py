"""Connector routes (M7 W3): operator rules that pick the agent for an inbound chat message.

A connector's config may carry an ordered list

    routes:
      - match: {field: text, regex: "^/ops\\b"}   # the webhook filter leaf / all / any / not
        agent: ops
        restrict: owners                           # default; `chat` lets any paired chat use it

The first matching route wins; no match means the connector's fixed `agent` (today's behaviour,
and the only behaviour for a connector without `routes`). `restrict: owners` needs the chat in
the connector's `owners` list and fails closed when that list is unset or empty. Routes are
operator configuration: they are checked strictly when the config is saved or a plugin starts
(`validate`) and leniently per message (`select` skips a bad route and says why).

The payload a filter sees is `{text (first 1000 characters), user, chat_id, name}`. `user` is a
display name the sender controls, so it is informational: authorization uses the chat id only.

Design: docs/designs/m7-agents-bots.md.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Collection, Iterator
from pathlib import Path
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, ValidationError

from auoreo.automation.templating import filter_matches
from auoreo.harness.manifests_webhook import WebhookFilter

log = logging.getLogger(__name__)

TEXT_CAP = 1000
"""Characters of the message text a filter sees (bounds regex work on user input)."""
REGEX_CAP = 200
WHYS = ("rule", "default", "invalid_route", "unknown_agent", "owners_only")
_warned: set[str] = set()


class RouteError(ValueError):
    """A route that cannot be used; `pointer` is its JSON pointer inside the plugin config."""

    def __init__(self, pointer: str, message: str) -> None:
        super().__init__(f"{pointer}: {message}")
        self.pointer = pointer


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    match: WebhookFilter
    agent: str
    restrict: Literal["owners", "chat"] = "owners"


class Decision(NamedTuple):
    agent: str | None
    """The routed agent, or None for the connector's fixed agent."""
    why: str


def _leaves(f: WebhookFilter) -> Iterator[WebhookFilter]:
    children = [*(f.all_ or []), *(f.any_ or []), *([f.not_] if f.not_ is not None else [])]
    if not children:
        yield f
    for child in children:
        yield from _leaves(child)


def parse(raw: Any) -> Route:
    """`raw` as a usable route; raises `ValueError` with a one-line reason otherwise."""
    if not isinstance(raw, dict):
        raise ValueError("a route is a mapping with `match` and `agent`")
    try:
        route = Route.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise ValueError(f"{where}: {first['msg']}" if where else first["msg"]) from None
    for leaf in _leaves(route.match):
        if leaf.in_file is not None:
            raise ValueError("`in_file` is not available in connector routes")
        if leaf.regex is None:
            continue
        if len(leaf.regex) > REGEX_CAP:
            raise ValueError(f"regex is longer than {REGEX_CAP} characters")
        try:
            re.compile(leaf.regex)
        except re.error as exc:
            raise ValueError(f"regex does not compile: {exc}") from None
    return route


def validate(config: dict[str, Any], agents: Collection[str] | None) -> None:
    """Strict check for save time and plugin start: the first bad route raises `RouteError`.

    `agents` are the known agent names; None (no registry at hand) checks the shape only.
    """
    raw = config.get("routes")
    if raw is None:
        return
    if not isinstance(raw, list):
        raise RouteError("/routes", "must be a list of routes")
    for i, item in enumerate(raw):
        try:
            route = parse(item)
        except ValueError as exc:
            raise RouteError(f"/routes/{i}", str(exc)) from None
        if agents is not None and route.agent not in agents:
            known = ", ".join(sorted(agents))
            raise RouteError(f"/routes/{i}/agent", f"unknown agent '{route.agent}' (known: {known})")


def payload_of(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": str(params.get("text", ""))[:TEXT_CAP],
        "user": str(params.get("user", "")),
        "chat_id": str(params.get("chat_id", "")),
        "name": str(params.get("name") or ""),
    }


def is_owner(config: dict[str, Any], chat_id: str) -> bool:
    """Is `chat_id` in the connector's `owners`? Unset or empty means no (fail closed)."""
    owners = config.get("owners")
    return isinstance(owners, list) and chat_id in {str(o) for o in owners}


def _warn_once(raw: Any, reason: str) -> None:
    key = f"{reason}:{json.dumps(raw, sort_keys=True, default=str)}"
    if key not in _warned:
        _warned.add(key)
        log.warning("connector route skipped (%s): %s", reason, key[len(reason) + 1 :][:300])


def select(routes: Any, payload: dict[str, Any], *, owner: bool, agents: Collection[str]) -> Decision:
    """First usable matching route → `(agent, "rule")`; else `(None, why)` for the fixed agent.

    A bad route is skipped (never raised): `why` is then `invalid_route`, `unknown_agent` (the
    route matched but names no agent) or `owners_only` (it matched but the chat is not an owner
    chat), taken from the first route skipped, when nothing later matched.
    """
    if not routes:
        return Decision(None, "default")
    if not isinstance(routes, list):
        _warn_once(routes, "invalid_route: `routes` is not a list")
        return Decision(None, "invalid_route")
    skipped: list[str] = []
    for raw in routes:
        try:
            route = parse(raw)
        except ValueError as exc:
            _warn_once(raw, f"invalid_route: {exc}")
            skipped.append("invalid_route")
            continue
        if not filter_matches(route.match, payload, None):
            continue
        if route.agent not in agents:
            _warn_once(raw, "unknown_agent")
            skipped.append("unknown_agent")
        elif route.restrict == "owners" and not owner:
            skipped.append("owners_only")
        else:
            return Decision(route.agent, "rule")
    return Decision(None, skipped[0] if skipped else "default")


def session_key(chat_id: str, agent: str | None) -> str:
    """Key in `sessions.json`: the bare chat id for the fixed agent (what older builds read and
    write), `<chat_id>:<agent>` for a routed one."""
    return f"{chat_id}:{agent}" if agent else chat_id


def session_agents(path: Path, fixed: str) -> dict[str, str]:
    """`session id → agent` for a connector's `sessions.json` (approval prompts name the agent)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, str] = {}
    for key, sid in data.items() if isinstance(data, dict) else ():
        _, sep, agent = str(key).rpartition(":")
        out[str(sid)] = agent if sep else fixed
    return out


__all__ = [
    "REGEX_CAP",
    "TEXT_CAP",
    "WHYS",
    "Decision",
    "Route",
    "RouteError",
    "is_owner",
    "parse",
    "payload_of",
    "select",
    "session_agents",
    "session_key",
    "validate",
]
