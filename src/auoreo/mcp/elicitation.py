"""MCP elicitation → AUOREO questions (spec §25.15 L1073, §26.14).

Form mode: the server's `message` + flat `requested_schema` become one `question.asked`
card (first question Accept/Decline, then one question per field) for the session whose
tool call is running. Accept → `content` coerced to the schema types; Decline → decline;
timeout / no answer → cancel. Nothing is ever pre-filled. A form that asks for a secret
(`format: password|secret`, `writeOnly`, or a field named like `*token`/`*key`/`*password`)
is refused outright — secrets MUST use URL mode, never a form.

URL mode: a consent card shows the server, message, full URL and its domain. On "Open" the
`mcp.elicitation.open` event asks the UI to open it in an external browser (AUOREO never
fetches or prefetches it) and the server gets `accept`; otherwise `decline`/`cancel`.

`-32042` (URL elicitation required) on a tool call: each listed URL is presented the same
way, then AUOREO waits for `notifications/elicitation/complete` for all of them (or a manual
"Retry now"/"Cancel" answer, or `URL_WAIT_S`) and retries the call — at most
`MAX_ELICIT_RETRIES` times. Every elicitation is audited `mcp.elicitation{server, mode,
outcome}`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from mcp.shared.exceptions import MCPError
from mcp.types import ElicitResult

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

ACCEPT, DECLINE = "Accept", "Decline"
OPEN, RETRY, CANCEL = "Open in browser", "Retry now", "Cancel"
URL_ELICITATION_REQUIRED = -32042
MAX_ELICIT_RETRIES = 3
URL_WAIT_S = 300.0
EXT_KEY = "mcp.elicitation"
SECRET_MESSAGE = "This form asks for a secret; secrets must use URL mode (never a form)."
SECRET_FORMATS = frozenset({"password", "secret"})
SECRET_NAME = re.compile(r"(?i)(token|key|passw(or)?d|passphrase|secret|credential|pin|otp|cvv|cvc)s?$")


def _result(action: str, content: dict[str, Any] | None = None, message: str | None = None) -> ElicitResult:
    data: dict[str, Any] = {"action": action}
    if content is not None:
        data["content"] = content
    if message:
        data["_meta"] = {"auoreo/message": message}
    return ElicitResult.model_validate(data)


async def audit(services: Services, sid: str | None, server: str, mode: str, outcome: str) -> None:
    detail = {"server": server, "mode": mode, "outcome": outcome}
    await services.audit.record(
        actor="mcp", action="mcp.elicitation", session_id=sid, decision=outcome, detail=detail
    )


# ------------------------------------------------------------------ form mode
def secret_fields(schema: dict[str, Any]) -> list[str]:
    """Fields of a form schema that ask for a secret (refused: secrets use URL mode)."""
    out = []
    for name, prop in (schema.get("properties") or {}).items():
        prop = prop if isinstance(prop, dict) else {}
        if (
            str(prop.get("format", "")).lower() in SECRET_FORMATS
            or prop.get("writeOnly") is True
            or SECRET_NAME.search(str(name))
        ):
            out.append(str(name))
    return out


def form_questions(server: str, message: str, schema: dict[str, Any]) -> list[dict[str, Any]]:
    qs: list[dict[str, Any]] = [
        {
            "question": f"MCP server '{server}' asks: {message}",
            "header": "MCP request",
            "options": [{"label": ACCEPT}, {"label": DECLINE, "description": "Refuse this request"}],
        }
    ]
    required = set(schema.get("required") or [])
    for name, prop in (schema.get("properties") or {}).items():
        label = str(prop.get("title") or name)
        desc = prop.get("description")
        text = label + (" (required)" if name in required else "") + (f" — {desc}" if desc else "")
        q: dict[str, Any] = {"question": text, "header": name[:20]}
        choices = prop.get("enum") or (prop.get("items") or {}).get("enum")
        if prop.get("type") == "boolean":
            choices = ["yes", "no"]
        if choices:
            q["options"] = [{"label": str(c)} for c in choices]
            q["multiSelect"] = prop.get("type") == "array"
        qs.append(q)
    return qs


def _bool(answer: str) -> bool:
    return answer.strip().lower() in {"yes", "true", "1", "y"}


_COERCE: dict[str, Callable[[str], Any]] = {
    "integer": int,
    "number": float,
    "boolean": _bool,
    "string": str,
}


def coerce(schema: dict[str, Any], responses: list[dict[str, Any]]) -> dict[str, Any]:
    """Map responses (index 1.. = fields in schema order) to typed content."""
    content: dict[str, Any] = {}
    for i, (name, prop) in enumerate((schema.get("properties") or {}).items(), start=1):
        if i >= len(responses):
            break
        resp = responses[i]
        answers = [str(a) for a in resp.get("answers") or [] if str(a)]
        answer = str(resp.get("answer") or (answers[0] if answers else "")).strip()
        if prop.get("type") == "array":
            content[name] = answers or [a.strip() for a in answer.split(",") if a.strip()]
            continue
        if not answer:
            continue
        try:
            content[name] = _COERCE.get(str(prop.get("type")), str)(answer)
        except ValueError:
            log.info("elicitation: cannot coerce %r for field %s", answer, name)
    return content


def _picked(answer: dict[str, Any]) -> list[str] | None:
    """Labels chosen for the first question; None on timeout / no answer."""
    responses = answer.get("responses") or []
    if answer.get("timed_out") or not responses:
        return None
    first = responses[0]
    return [str(a) for a in first.get("answers") or []] or [str(first.get("answer") or "")]


async def _form(services: Services, sid: str, server: str, params: Any) -> tuple[ElicitResult, str]:
    schema = dict(params.requested_schema or {})
    if secrets := secret_fields(schema):
        log.warning("MCP server '%s' asked for secret field(s) %s by form: refused", server, secrets)
        return _result("decline", message=SECRET_MESSAGE), "refused_secret"
    answer = await services.questions.ask(sid, form_questions(server, params.message, schema))
    picked = _picked(answer)
    if picked is None:
        return _result("cancel"), "cancel"
    if DECLINE in picked:
        return _result("decline"), "decline"
    return _result("accept", coerce(schema, answer.get("responses") or [])), "accept"


# ------------------------------------------------------------------ URL mode
class UrlCompletions:
    """`notifications/elicitation/complete` waiters, keyed by (server, elicitation id)."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], asyncio.Event] = {}

    def expect(self, server: str, eid: str) -> asyncio.Event:
        return self._events.setdefault((server, eid), asyncio.Event())

    def complete(self, server: str, eid: str) -> None:
        self.expect(server, eid).set()

    def forget(self, server: str, eid: str) -> None:
        self._events.pop((server, eid), None)


def completions(services: Services) -> UrlCompletions:
    found = services.ext.get(EXT_KEY)
    if not isinstance(found, UrlCompletions):
        found = services.ext[EXT_KEY] = UrlCompletions()
    return found


def notification_handler(services: Services, server: str) -> Callable[[Any], Awaitable[None]]:
    """`Client(message_handler=…)`: records elicitation completions for `server`."""

    async def handle(message: Any) -> None:
        msg = getattr(message, "root", message)
        if getattr(msg, "method", None) != "notifications/elicitation/complete":
            await asyncio.sleep(0)
            return
        eid = getattr(getattr(msg, "params", None), "elicitation_id", None)
        if eid:
            completions(services).complete(server, str(eid))

    return handle


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


async def consent_and_open(
    services: Services, sid: str, server: str, message: str, url: str, eid: str
) -> str:
    """Consent card → `mcp.elicitation.open` for the UI. Returns accept|decline|cancel."""
    if urlsplit(url).scheme not in {"https", "http"}:
        return "decline"
    domain = domain_of(url)
    card = {
        "question": f"MCP server '{server}': {message}\nOpen {url}\n(domain: {domain}) in your browser?",
        "header": "MCP link",
        "options": [{"label": OPEN, "description": domain}, {"label": DECLINE}],
    }
    picked = _picked(await services.questions.ask(sid, [card]))
    if picked is None:
        return "cancel"
    if OPEN not in picked:
        return "decline"
    data = {"server": server, "url": url, "domain": domain, "elicitation_id": eid}
    await services.emit(sid, "mcp.elicitation.open", data)
    return "accept"


async def _url(services: Services, sid: str, server: str, params: Any) -> tuple[ElicitResult, str]:
    eid = str(getattr(params, "elicitation_id", None) or uuid.uuid4().hex)
    outcome = await consent_and_open(services, sid, server, params.message, params.url, eid)
    return _result(outcome), outcome


_MODES: dict[str, Callable[..., Awaitable[tuple[ElicitResult, str]]]] = {"form": _form, "url": _url}


def make_elicitation_callback(
    services: Services, server: str, fallback_session: Callable[[], str | None]
) -> Callable[..., Any]:
    async def callback(context: Any, params: Any) -> ElicitResult:
        from auoreo.tools.context import CURRENT_SESSION

        mode = str(getattr(params, "mode", None) or "form")
        sid = CURRENT_SESSION.get() or fallback_session()
        if sid is None:
            await audit(services, None, server, mode, "no_session")
            return _result("decline", message="no AUOREO session is waiting on this server")
        handler = _MODES.get(mode)
        if handler is None:
            await audit(services, sid, server, mode, "unsupported")
            return _result("decline", message=f"unsupported elicitation mode {mode!r}")
        result, outcome = await handler(services, sid, server, params)
        await audit(services, sid, server, mode, outcome)
        return result

    return callback


# ------------------------------------------------------------------ -32042 retry loop
async def _await_completion(services: Services, sid: str, server: str, eids: list[str]) -> str:
    """completed | retry | cancel | timeout — the first of: every completion notification,
    a manual "Retry now"/"Cancel" answer, or `URL_WAIT_S`."""
    reg = completions(services)
    events = [reg.expect(server, e) for e in eids]
    card = {
        "question": f"Finish the step(s) for MCP server '{server}' in your browser, then retry.",
        "header": "MCP link",
        "options": [{"label": RETRY}, {"label": CANCEL}],
    }

    async def all_done() -> None:
        for e in events:
            await e.wait()

    done = asyncio.create_task(all_done())
    manual = asyncio.create_task(services.questions.ask(sid, [card], timeout_s=URL_WAIT_S))
    try:
        finished, _ = await asyncio.wait(
            {done, manual}, timeout=URL_WAIT_S, return_when=asyncio.FIRST_COMPLETED
        )
        if done in finished:
            return "completed"
        if manual in finished:
            picked = _picked(manual.result())
            return "retry" if picked and RETRY in picked else ("cancel" if picked else "timeout")
        return "timeout"
    finally:
        for t in (done, manual):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        for e in eids:
            reg.forget(server, e)


async def call_with_elicitation(
    services: Services, server: str, sid: str | None, call: Callable[[], Awaitable[Any]]
) -> Any:
    """Run `call`; on `-32042` present the URL elicitations, await completion and retry
    (≤ `MAX_ELICIT_RETRIES`). Raises `ElicitationFailed` when the user declines/cancels."""
    for attempt in range(MAX_ELICIT_RETRIES + 1):
        try:
            return await call()
        except MCPError as exc:
            if exc.code != URL_ELICITATION_REQUIRED or sid is None:
                raise
            if attempt == MAX_ELICIT_RETRIES:
                await audit(services, sid, server, "url", "retry_cap")
                raise ElicitationFailed(f"still requires URL elicitation after {attempt} retries") from exc
            items = list((exc.data or {}).get("elicitations") or []) if isinstance(exc.data, dict) else []
            eids: list[str] = []
            for item in items:
                eid = str(item.get("elicitationId") or item.get("elicitation_id") or uuid.uuid4().hex)
                url, message = str(item.get("url") or ""), str(item.get("message") or "")
                outcome = await consent_and_open(services, sid, server, message, url, eid)
                await audit(services, sid, server, "url", outcome)
                if outcome != "accept":
                    raise ElicitationFailed(f"declined/cancelled the URL elicitation ({outcome})") from exc
                eids.append(eid)
            waited = await _await_completion(services, sid, server, eids) if eids else "retry"
            await audit(services, sid, server, "url", f"wait_{waited}")
            if waited not in {"completed", "retry"}:
                raise ElicitationFailed(f"declined/cancelled the URL elicitation ({waited})") from exc
    raise AssertionError("unreachable")  # pragma: no cover


class ElicitationFailed(Exception):
    """The user declined/cancelled a required URL elicitation (do not retry)."""
