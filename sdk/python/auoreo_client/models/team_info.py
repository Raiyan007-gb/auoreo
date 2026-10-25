from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="TeamInfo")


@_attrs_define
class TeamInfo:
    """
    Attributes:
        created_at (datetime.datetime):
        id (str):
        lead_session_id (str):
        name (str):
        description (str | Unset):  Default: ''.
        members (int | Unset):  Default: 0.
    """

    created_at: datetime.datetime
    id: str
    lead_session_id: str
    name: str
    description: str | Unset = ""
    members: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = self.id

        lead_session_id = self.lead_session_id

        name = self.name

        description = self.description

        members = self.members

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "lead_session_id": lead_session_id,
                "name": name,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if members is not UNSET:
            field_dict["members"] = members

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        lead_session_id = d.pop("lead_session_id")

        name = d.pop("name")

        description = d.pop("description", UNSET)

        members = d.pop("members", UNSET)

        team_info = cls(
            created_at=created_at,
            id=id,
            lead_session_id=lead_session_id,
            name=name,
            description=description,
            members=members,
        )

        team_info.additional_properties = d
        return team_info

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
