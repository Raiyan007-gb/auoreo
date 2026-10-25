"""MCP elicitation → AUOREO questions (spec §26.14).

Form mode: the server's `message` + flat `requested_schema` become one `question.asked`
card (first question Accept/Decline, then one question per field) for the session whose
tool call is running. Accept → `content` coerced to the schema types; Decline → decline;
timeout / no answer → cancel. Nothing is ever pre-filled (secrets are never auto-filled).

URL mode: announced with `question.asked{mode: "url", url}` and declined at once with a
message that the user must complete it in a browser; the URL is never fetched by us.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mcp.types import ElicitResult

if TYPE_CHECKING:
    from auoreo.core.services import Services

log = logging.getLogger(__name__)

ACCEPT, DECLINE = "Accept", "Decline"
URL_MESSAGE = "The user must complete this request in a browser; AUOREO never opens or fetches it."


def _result(action: str, content: dict[str, Any] | None = None, message: str | None = None) -> ElicitResult:
    data: dict[str, Any] = {"action": action}
    if content is not None:
        data["content"] = content
    if message:
        data["_meta"] = {"auoreo/message": message}
    return ElicitResult.model_validate(data)


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


def make_elicitation_callback(
    services: Services, server: str, fallback_session: Callable[[], str | None]
) -> Callable[..., Any]:
    async def callback(context: Any, params: Any) -> ElicitResult:
        from auoreo.tools.context import CURRENT_SESSION

        sid = CURRENT_SESSION.get() or fallback_session()
        if sid is None:
            return _result("decline", message="no AUOREO session is waiting on this server")
        if getattr(params, "mode", "form") == "url":
            question = f"MCP server '{server}': {params.message}\nOpen in your browser: {params.url}"
            await services.emit(
                sid,
                "question.asked",
                {
                    "request_id": uuid.uuid4().hex,
                    "mode": "url",
                    "server": server,
                    "url": params.url,
                    "questions": [{"question": question, "header": "MCP link"}],
                },
            )
            return _result("decline", message=URL_MESSAGE)
        schema = dict(params.requested_schema or {})
        answer = await services.questions.ask(sid, form_questions(server, params.message, schema))
        responses = answer.get("responses") or []
        if answer.get("timed_out") or not responses:
            return _result("cancel")
        first = responses[0]
        picked = [str(a) for a in first.get("answers") or []] or [str(first.get("answer") or "")]
        if DECLINE in picked:
            return _result("decline")
        return _result("accept", coerce(schema, responses))

    return callback
