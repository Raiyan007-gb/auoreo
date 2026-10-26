from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.prefs_proposal_action import PrefsProposalAction
from ..models.prefs_proposal_status import PrefsProposalStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="PrefsProposal")


@_attrs_define
class PrefsProposal:
    """
    Attributes:
        action (PrefsProposalAction):
        content (str):
        created_at (float):
        id (str):
        status (PrefsProposalStatus):
        actor (str | Unset):  Default: 'agent'.
        approval_event_id (int | None | Unset): `seq` of the `prefs.approved` event, also written into the PREFS.md
            entry
        decided_at (float | None | Unset):
        decided_by (None | str | Unset):
        deny_reason (None | str | Unset):
        old_text (None | str | Unset):
        reason (str | Unset):  Default: ''.
        session_id (None | str | Unset):
    """

    action: PrefsProposalAction
    content: str
    created_at: float
    id: str
    status: PrefsProposalStatus
    actor: str | Unset = "agent"
    approval_event_id: int | None | Unset = UNSET
    decided_at: float | None | Unset = UNSET
    decided_by: None | str | Unset = UNSET
    deny_reason: None | str | Unset = UNSET
    old_text: None | str | Unset = UNSET
    reason: str | Unset = ""
    session_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        action = self.action.value

        content = self.content

        created_at = self.created_at

        id = self.id

        status = self.status.value

        actor = self.actor

        approval_event_id: int | None | Unset
        if isinstance(self.approval_event_id, Unset):
            approval_event_id = UNSET
        else:
            approval_event_id = self.approval_event_id

        decided_at: float | None | Unset
        if isinstance(self.decided_at, Unset):
            decided_at = UNSET
        else:
            decided_at = self.decided_at

        decided_by: None | str | Unset
        if isinstance(self.decided_by, Unset):
            decided_by = UNSET
        else:
            decided_by = self.decided_by

        deny_reason: None | str | Unset
        if isinstance(self.deny_reason, Unset):
            deny_reason = UNSET
        else:
            deny_reason = self.deny_reason

        old_text: None | str | Unset
        if isinstance(self.old_text, Unset):
            old_text = UNSET
        else:
            old_text = self.old_text

        reason = self.reason

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "action": action,
                "content": content,
                "created_at": created_at,
                "id": id,
                "status": status,
            }
        )
        if actor is not UNSET:
            field_dict["actor"] = actor
        if approval_event_id is not UNSET:
            field_dict["approval_event_id"] = approval_event_id
        if decided_at is not UNSET:
            field_dict["decided_at"] = decided_at
        if decided_by is not UNSET:
            field_dict["decided_by"] = decided_by
        if deny_reason is not UNSET:
            field_dict["deny_reason"] = deny_reason
        if old_text is not UNSET:
            field_dict["old_text"] = old_text
        if reason is not UNSET:
            field_dict["reason"] = reason
        if session_id is not UNSET:
            field_dict["session_id"] = session_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        action = PrefsProposalAction(d.pop("action"))

        content = d.pop("content")

        created_at = d.pop("created_at")

        id = d.pop("id")

        status = PrefsProposalStatus(d.pop("status"))

        actor = d.pop("actor", UNSET)

        def _parse_approval_event_id(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        approval_event_id = _parse_approval_event_id(d.pop("approval_event_id", UNSET))

        def _parse_decided_at(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        decided_at = _parse_decided_at(d.pop("decided_at", UNSET))

        def _parse_decided_by(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        decided_by = _parse_decided_by(d.pop("decided_by", UNSET))

        def _parse_deny_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        deny_reason = _parse_deny_reason(d.pop("deny_reason", UNSET))

        def _parse_old_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        old_text = _parse_old_text(d.pop("old_text", UNSET))

        reason = d.pop("reason", UNSET)

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        prefs_proposal = cls(
            action=action,
            content=content,
            created_at=created_at,
            id=id,
            status=status,
            actor=actor,
            approval_event_id=approval_event_id,
            decided_at=decided_at,
            decided_by=decided_by,
            deny_reason=deny_reason,
            old_text=old_text,
            reason=reason,
            session_id=session_id,
        )

        prefs_proposal.additional_properties = d
        return prefs_proposal

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
