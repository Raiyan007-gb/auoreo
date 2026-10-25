from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.audit_entry_detail import AuditEntryDetail


T = TypeVar("T", bound="AuditEntry")


@_attrs_define
class AuditEntry:
    """
    Attributes:
        action (str):
        actor (str):
        prev_hash (str):
        row_hash (str):
        seq (int):
        ts (datetime.datetime):
        decision (str | Unset):  Default: ''.
        detail (AuditEntryDetail | Unset): Redacted detail payload
        reason (str | Unset):  Default: ''.
        session_id (None | str | Unset):
    """

    action: str
    actor: str
    prev_hash: str
    row_hash: str
    seq: int
    ts: datetime.datetime
    decision: str | Unset = ""
    detail: AuditEntryDetail | Unset = UNSET
    reason: str | Unset = ""
    session_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        action = self.action

        actor = self.actor

        prev_hash = self.prev_hash

        row_hash = self.row_hash

        seq = self.seq

        ts = self.ts.isoformat()

        decision = self.decision

        detail: dict[str, Any] | Unset = UNSET
        if not isinstance(self.detail, Unset):
            detail = self.detail.to_dict()

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
                "actor": actor,
                "prev_hash": prev_hash,
                "row_hash": row_hash,
                "seq": seq,
                "ts": ts,
            }
        )
        if decision is not UNSET:
            field_dict["decision"] = decision
        if detail is not UNSET:
            field_dict["detail"] = detail
        if reason is not UNSET:
            field_dict["reason"] = reason
        if session_id is not UNSET:
            field_dict["session_id"] = session_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.audit_entry_detail import AuditEntryDetail  # noqa: PLC0415

        d = dict(src_dict)
        action = d.pop("action")

        actor = d.pop("actor")

        prev_hash = d.pop("prev_hash")

        row_hash = d.pop("row_hash")

        seq = d.pop("seq")

        ts = datetime.datetime.fromisoformat(d.pop("ts"))

        decision = d.pop("decision", UNSET)

        _detail = d.pop("detail", UNSET)
        detail: AuditEntryDetail | Unset
        if isinstance(_detail, Unset):
            detail = UNSET
        else:
            detail = AuditEntryDetail.from_dict(_detail)

        reason = d.pop("reason", UNSET)

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        audit_entry = cls(
            action=action,
            actor=actor,
            prev_hash=prev_hash,
            row_hash=row_hash,
            seq=seq,
            ts=ts,
            decision=decision,
            detail=detail,
            reason=reason,
            session_id=session_id,
        )

        audit_entry.additional_properties = d
        return audit_entry

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
