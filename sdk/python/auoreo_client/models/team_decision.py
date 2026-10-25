from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.team_decision_decision import TeamDecisionDecision
from ..types import UNSET, Unset

T = TypeVar("T", bound="TeamDecision")


@_attrs_define
class TeamDecision:
    """A supervisor's continue/spawn/finish decision (`team.decision` event).

    Attributes:
        child (str):
        created_at (datetime.datetime):
        decision (TeamDecisionDecision):
        outcome (str): `new` for a spawn, else the child's status or error code
        seq (int):
        team_id (str):
        reason (str | Unset):  Default: ''.
    """

    child: str
    created_at: datetime.datetime
    decision: TeamDecisionDecision
    outcome: str
    seq: int
    team_id: str
    reason: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        child = self.child

        created_at = self.created_at.isoformat()

        decision = self.decision.value

        outcome = self.outcome

        seq = self.seq

        team_id = self.team_id

        reason = self.reason

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "child": child,
                "created_at": created_at,
                "decision": decision,
                "outcome": outcome,
                "seq": seq,
                "team_id": team_id,
            }
        )
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        child = d.pop("child")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        decision = TeamDecisionDecision(d.pop("decision"))

        outcome = d.pop("outcome")

        seq = d.pop("seq")

        team_id = d.pop("team_id")

        reason = d.pop("reason", UNSET)

        team_decision = cls(
            child=child,
            created_at=created_at,
            decision=decision,
            outcome=outcome,
            seq=seq,
            team_id=team_id,
            reason=reason,
        )

        team_decision.additional_properties = d
        return team_decision

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
