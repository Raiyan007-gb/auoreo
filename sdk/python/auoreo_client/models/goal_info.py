from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.goal_info_status import GoalInfoStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="GoalInfo")


@_attrs_define
class GoalInfo:
    """
    Attributes:
        created_at (datetime.datetime):
        id (int):
        progress (float):
        status (GoalInfoStatus):
        title (str):
        updated_at (datetime.datetime):
        absorbed_into (int | None | Unset):
        description (str | Unset):  Default: ''.
        evidence_session_ids (list[str] | Unset):
        session_id (None | str | Unset):
    """

    created_at: datetime.datetime
    id: int
    progress: float
    status: GoalInfoStatus
    title: str
    updated_at: datetime.datetime
    absorbed_into: int | None | Unset = UNSET
    description: str | Unset = ""
    evidence_session_ids: list[str] | Unset = UNSET
    session_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = self.id

        progress = self.progress

        status = self.status.value

        title = self.title

        updated_at = self.updated_at.isoformat()

        absorbed_into: int | None | Unset
        if isinstance(self.absorbed_into, Unset):
            absorbed_into = UNSET
        else:
            absorbed_into = self.absorbed_into

        description = self.description

        evidence_session_ids: list[str] | Unset = UNSET
        if not isinstance(self.evidence_session_ids, Unset):
            evidence_session_ids = self.evidence_session_ids

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "progress": progress,
                "status": status,
                "title": title,
                "updated_at": updated_at,
            }
        )
        if absorbed_into is not UNSET:
            field_dict["absorbed_into"] = absorbed_into
        if description is not UNSET:
            field_dict["description"] = description
        if evidence_session_ids is not UNSET:
            field_dict["evidence_session_ids"] = evidence_session_ids
        if session_id is not UNSET:
            field_dict["session_id"] = session_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        progress = d.pop("progress")

        status = GoalInfoStatus(d.pop("status"))

        title = d.pop("title")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        def _parse_absorbed_into(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        absorbed_into = _parse_absorbed_into(d.pop("absorbed_into", UNSET))

        description = d.pop("description", UNSET)

        evidence_session_ids = cast(list[str], d.pop("evidence_session_ids", UNSET))

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        goal_info = cls(
            created_at=created_at,
            id=id,
            progress=progress,
            status=status,
            title=title,
            updated_at=updated_at,
            absorbed_into=absorbed_into,
            description=description,
            evidence_session_ids=evidence_session_ids,
            session_id=session_id,
        )

        goal_info.additional_properties = d
        return goal_info

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
