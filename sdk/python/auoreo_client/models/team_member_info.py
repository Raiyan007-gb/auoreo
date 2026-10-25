from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="TeamMemberInfo")


@_attrs_define
class TeamMemberInfo:
    """
    Attributes:
        agent (str):
        depth (int):
        name (str): Member name; "lead" is the team lead session
        running (bool): Liveness: a run is in progress right now
        session_id (str):
        status (str): lead | queued | running | done | failed | stopped
    """

    agent: str
    depth: int
    name: str
    running: bool
    session_id: str
    status: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        depth = self.depth

        name = self.name

        running = self.running

        session_id = self.session_id

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "depth": depth,
                "name": name,
                "running": running,
                "session_id": session_id,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent = d.pop("agent")

        depth = d.pop("depth")

        name = d.pop("name")

        running = d.pop("running")

        session_id = d.pop("session_id")

        status = d.pop("status")

        team_member_info = cls(
            agent=agent,
            depth=depth,
            name=name,
            running=running,
            session_id=session_id,
            status=status,
        )

        team_member_info.additional_properties = d
        return team_member_info

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
