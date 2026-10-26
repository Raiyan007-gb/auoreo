"""Leader→worker control messages (spec §25.11 permission ceiling): `team_permission_update`
and `mode_set_request`.

- Only a child's own parent may send them (the leader of that worker); anything else — a
  worker writing to its leader or a sibling — is a worker-originated widen: rejected + audited.
- `team_permission_update{rules:[{permission, pattern, action}]}`: permission names must be
  allow-listed (a known tool permission) and never one the child's ceiling denies (no allow
  resurrection). Rules are session-scoped (`engine.session_rules` of the child only).
- `mode_set_request{mode}`: the child's mode, clamped to its ceiling (`CeilingEngine`).

Both are stored as structured mailbox rows (never injected into the LLM context), emitted as
`team.control` and audited.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from auoreo.core.db import MailboxRow
from auoreo.core.permissions import Mode
from auoreo.harness.manifests import Rule
from auoreo.teams.ceiling import LADDER, CeilingEngine, clamp
from auoreo.teams.mailbox import TeamsError

if TYPE_CHECKING:
    from auoreo.teams.service import TeamsService

CONTROL_TYPES = ("team_permission_update", "mode_set_request")
ACTIONS = ("allow", "ask", "deny")
MODES = (*LADDER, "plan")
MAX_RULES = 20


class PinnedCeilingEngine(CeilingEngine):
    """A child engine whose mode the leader pinned (`mode_set_request`): the runner's
    per-turn refresh from the agent manifest no longer overrides it (plan mode still wins);
    the parent's ceiling still clamps it."""

    pinned: str | None = None

    @property
    def mode(self) -> Mode:
        return self._mode

    @mode.setter
    def mode(self, value: Mode) -> None:
        pinned = getattr(self, "pinned", None)
        self._mode = clamp(value if value == "plan" or not pinned else pinned, self.ceiling)


def _known_permissions(t: TeamsService) -> set[str]:
    return {str(tool.manifest.permission_name) for tool in t.s.harness.tools.values()} | set(
        t.s.harness.tools
    )


async def _target(t: TeamsService, sender: str, ref: str, kind: str, detail: dict[str, Any]) -> Any:
    try:
        return await t.control.child(sender, ref)
    except TeamsError as exc:
        await t.s.audit.record(
            actor="agent",
            action=f"team.{kind}",
            session_id=sender,
            decision="deny",
            reason="worker-originated or unknown target",
            detail={"to": ref, **detail},
        )
        raise TeamsError(f"{kind} is leader→worker only: '{ref}' is not one of your subagents") from exc


async def _record(t: TeamsService, sender: str, row: Any, kind: str, payload: dict[str, Any]) -> None:
    team = await t.team_for(sender)
    if team is not None:
        name = await t.name_in_team(team, sender)
        async with t.s.db.session() as s:
            s.add(
                MailboxRow(
                    team_id=team.id,
                    recipient=row.name,
                    sender=name,
                    type=kind,
                    summary=kind,
                    text=json.dumps(payload),
                    read=True,
                )
            )
            await s.commit()
    await t.s.audit.record(
        actor="agent",
        action=f"team.{kind}",
        session_id=sender,
        decision="allow",
        detail={"to": row.name, **payload},
    )
    await t.s.emit(sender, "team.control", {"type": kind, "to": row.name, "child": row.session_id, **payload})


async def permission_update(
    t: TeamsService, sender: str, ref: str, rules: list[dict[str, Any]]
) -> dict[str, Any]:
    row = await _target(t, sender, ref, "team_permission_update", {"rules": rules})
    if not rules or len(rules) > MAX_RULES:
        raise TeamsError(f"team_permission_update needs 1–{MAX_RULES} rules")
    engine = t.s.coordinator.engine_for(row.session_id)
    if engine is None:
        raise TeamsError(f"subagent '{row.name}' has no live permission engine yet")
    known, denied = _known_permissions(t), set(getattr(engine, "denied", ()))
    parsed: list[Rule] = []
    for raw in rules:
        perm, action = str(raw.get("permission", "")), str(raw.get("action", ""))
        if perm not in known:
            raise TeamsError(f"unknown permission name '{perm}' (not allow-listed)")
        if action not in ACTIONS:
            raise TeamsError(f"rule action must be one of {', '.join(ACTIONS)}")
        if perm in denied and action != "deny":
            raise TeamsError(f"'{perm}' is denied by the subagent's ceiling; it cannot be re-allowed")
        parsed.append(Rule(permission=perm, pattern=str(raw.get("pattern") or "*"), action=action))
    engine.session_rules = [*engine.session_rules, *parsed]
    payload = {"rules": [r.model_dump() for r in parsed]}
    await _record(t, sender, row, "team_permission_update", payload)
    return {"name": row.name, "subagent_id": row.subagent_id, **payload}


async def mode_set(t: TeamsService, sender: str, ref: str, mode: str) -> dict[str, Any]:
    row = await _target(t, sender, ref, "mode_set_request", {"mode": mode})
    if mode not in MODES:
        raise TeamsError(f"mode must be one of {', '.join(MODES)}")
    engine = t.s.coordinator.engine_for(row.session_id)
    if engine is None:
        raise TeamsError(f"subagent '{row.name}' has no live permission engine yet")
    if isinstance(engine, CeilingEngine):
        engine.__class__ = PinnedCeilingEngine  # survives the runner's per-turn mode refresh
        engine.pinned = mode
    engine.mode = mode  # type: ignore[assignment]  # clamped to the parent's ceiling
    effective = str(engine.mode)
    await _record(t, sender, row, "mode_set_request", {"mode": mode, "effective": effective})
    return {"name": row.name, "subagent_id": row.subagent_id, "mode": mode, "effective": effective}
