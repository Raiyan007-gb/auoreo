"""Suggestion delivery through the deliveries outbox (spec §16.7, L1028).

A suggestion is plain text, not an agent run, so it cannot go through `DeliveryWorker`
(which runs an agent per row). Instead each attempt is ONE outbox row inserted directly as
`delivering` by this process (the `(kind, source, external_id)` unique index is the
claim: a second process inserting the same attempt loses and skips), sent through the
same provider registry, and finished with `Outbox.finish` + `delivery.delivered|failed`,
so it shows up in `auoreo deliveries list` and `/api/deliveries` like any other row.
"""

from __future__ import annotations

from datetime import datetime, time
from typing import TYPE_CHECKING, Any

from sqlalchemy.exc import IntegrityError

from auoreo.automation.deliveries import outbox_of
from auoreo.backends import delivery as providers
from auoreo.core.db import DeliveryRow, SuggestionRow, utcnow

if TYPE_CHECKING:
    from auoreo.core.services import Services

KIND = "suggestion"
SOURCE = "suggestions"
MAX_TEXT = 4000
SPECIAL = frozenset({"origin", "home", "all"})


def parse_quiet(spec: str | None) -> tuple[time, time] | None:
    """`"22:00-07:00"` → (22:00, 07:00); None/empty → None. Raises ValueError when malformed."""
    if not spec:
        return None
    start, _, end = spec.partition("-")
    return time.fromisoformat(start.strip().zfill(5)), time.fromisoformat(end.strip().zfill(5))


def in_quiet(spec: str | None, now: datetime) -> bool:
    """Is local time `now` inside the window? A window may wrap midnight; start == end is empty."""
    window = parse_quiet(spec)
    if window is None:
        return False
    start, end = window
    t = now.time()
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def known_providers(services: Services) -> list[str]:
    return sorted({*services.harness.backends_for("delivery"), *providers.DYNAMIC})


def validate_deliver(services: Services, deliver: str) -> None:
    if deliver in SPECIAL:
        return
    name, _ = providers.split_spec(deliver)
    known = known_providers(services)
    if name not in known:
        raise ValueError(f"unknown delivery target '{deliver}' (use origin, home, all or one of {known})")


async def targets_for(services: Services, deliver: str) -> list[str]:
    """`home` → settings.suggestions_home; `all` → every provider whose check passes;
    anything else is one `<provider>[:<target>]` spec. `origin` delivers nowhere."""
    if deliver == "origin":
        return []
    if deliver == "home":
        return [services.settings.suggestions_home]
    if deliver != "all":
        return [deliver]
    out: list[str] = []
    for name in known_providers(services):
        try:
            status = await providers.resolve(services, name).check()
        except Exception:  # a broken provider is just not part of "all"
            continue
        if status.ok:
            out.append(name)
    return out or [services.settings.suggestions_home]


def render(row: SuggestionRow) -> str:
    head = f"[suggestion #{row.id}] {row.title}"
    tail = f"\n\nReply with consent: accept or deny suggestion #{row.id} (auoreo UI → Goals → Suggestions)."
    body = (row.body or "").strip()
    room = MAX_TEXT - len(head) - len(tail) - 2
    if len(body) > room:
        body = body[: max(room - 1, 0)] + "…"
    return (f"{head}\n\n{body}{tail}" if body else head + tail)[:MAX_TEXT]


async def send_once(
    services: Services, row: SuggestionRow, specs: list[str], suffix: str = ""
) -> tuple[int | None, bool]:
    """One outbox attempt for `row` to `specs` (ok when any target accepts it).
    Returns (delivery id, ok); (None, False) when another process already made this attempt."""
    text = render(row)
    external_id = f"suggestion-{row.id}{suffix}"
    now = utcnow()
    drow = DeliveryRow(
        kind=KIND,
        source=SOURCE,
        external_id=external_id,
        payload={"text": text, "deliver": specs, "suggestion_id": row.id},
        result={},
        target=(specs[0] if len(specs) == 1 else "all")[:64],
        status="delivering",
        attempts=1,
        claimed_at=now,
        session_id=row.origin_session,
    )
    try:
        async with services.db.session() as s:
            s.add(drow)
            await s.commit()
    except IntegrityError:
        return None, False
    meta: dict[str, Any] = {
        "kind": KIND,
        "source": SOURCE,
        "external_id": external_id,
        "delivery_id": drow.id,
        "session_id": row.origin_session,
        "suggestion_id": row.id,
        "buttons": [{"id": "accept", "label": "Accept"}, {"id": "deny", "label": "Deny"}],
    }
    await services.emit(
        "server",
        "delivery.queued",
        {"delivery_id": drow.id, "kind": KIND, "source": SOURCE, "external_id": external_id},
    )
    attempts: list[dict[str, Any]] = []
    for spec in specs:
        name, target = providers.split_spec(spec)
        try:
            receipt = await providers.resolve(services, name).send(target, text, meta=meta)
        except Exception as exc:  # a broken provider counts as a failed attempt
            receipt = providers.DeliveryReceipt(False, f"{exc.__class__.__name__}: {exc}"[:500])
        attempts.append({"provider": name, "ok": receipt.ok, "detail": receipt.detail})
    winner = next((a["provider"] for a in attempts if a["ok"]), None)
    error = None if winner else "; ".join(f"{a['provider']}: {a['detail']}" for a in attempts) or "no target"
    await outbox_of(services).finish(
        drow.id, winner is not None, {"provider": winner, "attempts": attempts}, error
    )
    status = "delivered" if winner else "failed"
    data = {k: meta[k] for k in ("kind", "source", "external_id", "delivery_id", "session_id")}
    await services.emit(
        "server", f"delivery.{status}", data | {"provider": winner} | ({"error": error} if error else {})
    )
    return drow.id, winner is not None
