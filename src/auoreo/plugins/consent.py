"""Consent-gated posting for public-network connectors (docs/designs/m7-agents-bots.md, Wave 2).

A plugin that declares `consumes: ["consent:post"]` (Mastodon) never answers in place:

- *Reply mode.* `InboundRouter.handle` runs the turn, then `propose_reply` records a
  suggestion of kind `post` whose body and `action.text` are the same string (key
  `post:<plugin>:<status id>`, deliver `home`). Chat commands, approval prompts and streaming
  are off for such a plugin. With `m7.agents.post_consent` off nothing is proposed.
- *Gate.* `PluginHost._sender` calls `gate` for every `delivery.send`: it is refused unless the
  call runs inside `post` for this plugin (a context variable no caller data can set), and
  `meta.consent_id` is overwritten with the id of the suggestion being posted, so a forged
  id in `meta` never reaches the plugin.
- *Post.* `post(services, suggestion_id)` (called by the suggestion `post` action on accept)
  checks that the row is accepted, of type `post`, for a running consent plugin and not yet
  sent, enforces `m7.agents.post_daily_cap` per plugin per UTC day, claims the row, sends once
  and records `sent` / `external_id` in the action. The plugin uses the consent id as the
  idempotency key. Nothing here writes the post text to an audit row.
"""

from __future__ import annotations

import asyncio
import contextvars
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from auoreo.core.db import SuggestionRow, utcnow
from auoreo.plugins.connectors import _read_json, _write_json

if TYPE_CHECKING:
    from auoreo.core.services import Services
    from auoreo.plugins.host import PluginState

CONSENT = "consent:post"
_ACTIVE: contextvars.ContextVar[tuple[str, int] | None] = contextvars.ContextVar(
    "auoreo_post_consent", default=None
)
_LOCK = asyncio.Lock()


class ConsentError(ValueError):
    """A refused post (flag off, not accepted, wrong plugin, already sent, over the cap)."""


def needs_consent(consumes: list[str]) -> bool:
    return CONSENT in consumes


def reply_mode(services: Services, plugin: str) -> bool:
    """Whether `plugin` declares `consent:post` (its replies become suggestions)."""
    loaded = services.harness.plugins.get(plugin)
    manifest = getattr(loaded, "manifest", None)
    return manifest is not None and needs_consent(list(manifest.consumes))


async def _audit(services: Services, plugin: str, decision: str, reason: str, detail: dict[str, Any]) -> None:
    await services.audit.record(
        actor=f"plugin:{plugin}", action="plugin.post", decision=decision, reason=reason, detail=detail
    )


async def gate(services: Services, st: PluginState, meta: dict[str, Any]) -> dict[str, Any] | None:
    """None = send; else the refusal receipt. Sets `meta.consent_id` from the active consent."""
    if not needs_consent(list(st.manifest.consumes)):
        return None
    active = _ACTIVE.get()
    if active is None or active[0] != st.name:
        forged = str(meta.get("consent_id", ""))[:32]
        await _audit(services, st.name, "deny", "no accepted suggestion", {"claimed_consent_id": forged})
        return {"ok": False, "detail": "refused: this connector posts only for an accepted suggestion"}
    meta["consent_id"] = str(active[1])
    return None


# ---------------------------------------------------------------- reply mode
async def propose_reply(
    services: Services, plugin: str, params: dict[str, Any], reply: str, session_id: str
) -> dict[str, Any]:
    """Record the drafted reply as a `post` suggestion instead of sending it."""
    from auoreo.suggestions.engine import SuggestionError, suggestions_of

    chat_id = str(params.get("chat_id", ""))
    raw = params.get("reply_to")
    reply_to: dict[str, Any] = raw if isinstance(raw, dict) else {}
    status_id = str(reply_to.get("status_id") or "")[:64]
    base = {"ok": False, "session_id": session_id, "proposed": None}
    if not services.settings.m7.agents.post_consent:
        await _audit(services, plugin, "deny", "m7.agents.post_consent is off", {"chat_id": chat_id[:64]})
        return {**base, "detail": "post_consent is off; nothing proposed"}
    if not reply.strip():
        return {**base, "detail": "empty draft"}
    action = {
        "type": "post",
        "plugin": plugin,
        "target": chat_id,
        "text": reply,
        "reply_to": {k: v for k, v in reply_to.items() if isinstance(v, str | int)},
    }
    try:
        row, created = await suggestions_of(services).propose(
            "post",
            f"Reply to {chat_id} on {plugin}"[:300],
            reply,
            deliver="home",
            origin_session=session_id,
            action=action,
            dedupe_key=f"post:{plugin}:{status_id or session_id}",
        )
    except SuggestionError as exc:
        return {**base, "detail": str(exc)}
    return {
        "ok": True,
        "session_id": session_id,
        "proposed": row.id,
        "created": created,
        "detail": "proposed",
    }


# ---------------------------------------------------------------------- post
def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


async def _set_action(services: Services, suggestion_id: int, **changes: Any) -> None:
    async with services.db.session() as s:
        row = await s.get(SuggestionRow, suggestion_id)
        if row is not None:
            row.action = {**(row.action or {}), **changes}
            row.updated_at = utcnow()
            await s.commit()


async def post(services: Services, suggestion_id: int) -> dict[str, Any]:
    """Send one accepted `post` suggestion through its plugin; raises `ConsentError`."""
    if not services.settings.m7.agents.post_consent:
        raise ConsentError("m7.agents.post_consent is off")
    async with _LOCK:
        async with services.db.session() as s:
            row = (
                await s.execute(select(SuggestionRow).where(SuggestionRow.id == suggestion_id))
            ).scalar_one_or_none()
        action = dict(row.action or {}) if row is not None else {}
        plugin = str(action.get("plugin", ""))
        st = _plugin_state(services, plugin)
        reason = _refusal(row, action, st)
        detail = {"suggestion_id": suggestion_id, "plugin": plugin[:64]}
        if reason is None:
            reason = _charge(services, plugin)
        if reason is not None:
            await _audit(services, plugin or "unknown", "deny", reason, detail)
            raise ConsentError(f"suggestion {suggestion_id}: {reason}")
        await _set_action(services, suggestion_id, sent="claimed")
    assert st is not None
    host = services.ext["host"]
    delivery = host.deliveries.get(st.deliveries[0])
    if delivery is None:
        await _set_action(services, suggestion_id, sent=None)
        raise ConsentError(f"suggestion {suggestion_id}: the plugin has no delivery")
    meta = {"reply_to": action.get("reply_to"), "connector": plugin}
    token = _ACTIVE.set((plugin, suggestion_id))
    try:
        receipt = await delivery.send(str(action.get("target", "")), str(action.get("text", "")), meta=meta)
    finally:
        _ACTIVE.reset(token)
    ok, external = bool(getattr(receipt, "ok", False)), getattr(receipt, "external_id", None)
    await _set_action(
        services, suggestion_id, sent="ok" if ok else None, external_id=external, sent_at=utcnow().isoformat()
    )
    await _audit(services, plugin, "allow" if ok else "error", "consent", {**detail, "external_id": external})
    return {"ok": ok, "detail": str(getattr(receipt, "detail", "")), "external_id": external}


def _plugin_state(services: Services, plugin: str) -> PluginState | None:
    host = services.ext.get("host")
    plugins = getattr(host, "plugins", None)
    st = plugins.get(plugin) if isinstance(plugins, dict) else None
    return st if st is not None and st.status == "running" and st.deliveries else None


def _refusal(row: SuggestionRow | None, action: dict[str, Any], st: PluginState | None) -> str | None:
    checks = (
        (row is None, "not found"),
        (row is not None and row.status != "accepted", "not accepted"),
        (action.get("type") != "post", "not a post suggestion"),
        (st is None, "the plugin is not running"),
        (
            st is not None and not needs_consent(list(st.manifest.consumes)),
            "plugin does not consume consent:post",
        ),
        (bool(action.get("sent")), "already sent"),
        (not str(action.get("text") or "").strip(), "empty text"),
    )
    return next((why for bad, why in checks if bad), None)


def _charge(services: Services, plugin: str) -> str | None:
    """Count one post against `post_daily_cap` (per plugin, UTC day); a reason when over."""
    path = services.settings.home / "plugins" / plugin / "post_quota.json"
    quota = _read_json(path)
    used = int(quota.get("count", 0)) if quota.get("day") == _today() else 0
    if used >= services.settings.m7.agents.post_daily_cap:
        return "daily post cap reached"
    _write_json(path, {"day": _today(), "count": used + 1})
    return None
