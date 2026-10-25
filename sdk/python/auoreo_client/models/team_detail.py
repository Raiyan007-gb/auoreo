from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.team_member_info import TeamMemberInfo


T = TypeVar("T", bound="TeamDetail")


@_attrs_define
class TeamDetail:
    """
    Attributes:
        created_at (datetime.datetime):
        id (str):
        lead_session_id (str):
        name (str):
        description (str | Unset):  Default: ''.
        members (int | Unset):  Default: 0.
        peers (list[TeamMemberInfo] | Unset):
    """

    created_at: datetime.datetime
    id: str
    lead_session_id: str
    name: str
    description: str | Unset = ""
    members: int | Unset = 0
    peers: list[TeamMemberInfo] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = self.id

        lead_session_id = self.lead_session_id

        name = self.name

        description = self.description

        members = self.members

        peers: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.peers, Unset):
            peers = []
            for peers_item_data in self.peers:
                peers_item = peers_item_data.to_dict()
                peers.append(peers_item)

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
        if peers is not UNSET:
            field_dict["peers"] = peers

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.team_member_info import TeamMemberInfo  # noqa: PLC0415

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        lead_session_id = d.pop("lead_session_id")

        name = d.pop("name")

        description = d.pop("description", UNSET)

        members = d.pop("members", UNSET)

        _peers = d.pop("peers", UNSET)
        peers: list[TeamMemberInfo] | Unset = UNSET
        if _peers is not UNSET:
            peers = []
            for peers_item_data in _peers:
                peers_item = TeamMemberInfo.from_dict(peers_item_data)

                peers.append(peers_item)

        team_detail = cls(
            created_at=created_at,
            id=id,
            lead_session_id=lead_session_id,
            name=name,
            description=description,
            members=members,
            peers=peers,
        )

        team_detail.additional_properties = d
        return team_detail

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
