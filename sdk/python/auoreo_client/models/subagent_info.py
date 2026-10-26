from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.subagent_info_usage import SubagentInfoUsage


T = TypeVar("T", bound="SubagentInfo")


@_attrs_define
class SubagentInfo:
    """
    Attributes:
        agent (str):
        depth (int):
        name (str):
        role (None | str):
        running (bool):
        status (str):
        subagent_id (str):
        usage (SubagentInfoUsage | Unset):
    """

    agent: str
    depth: int
    name: str
    role: None | str
    running: bool
    status: str
    subagent_id: str
    usage: SubagentInfoUsage | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        depth = self.depth

        name = self.name

        role: None | str
        role = self.role

        running = self.running

        status = self.status

        subagent_id = self.subagent_id

        usage: dict[str, Any] | Unset = UNSET
        if not isinstance(self.usage, Unset):
            usage = self.usage.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "depth": depth,
                "name": name,
                "role": role,
                "running": running,
                "status": status,
                "subagent_id": subagent_id,
            }
        )
        if usage is not UNSET:
            field_dict["usage"] = usage

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.subagent_info_usage import SubagentInfoUsage  # noqa: PLC0415

        d = dict(src_dict)
        agent = d.pop("agent")

        depth = d.pop("depth")

        name = d.pop("name")

        def _parse_role(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        role = _parse_role(d.pop("role"))

        running = d.pop("running")

        status = d.pop("status")

        subagent_id = d.pop("subagent_id")

        _usage = d.pop("usage", UNSET)
        usage: SubagentInfoUsage | Unset
        if isinstance(_usage, Unset):
            usage = UNSET
        else:
            usage = SubagentInfoUsage.from_dict(_usage)

        subagent_info = cls(
            agent=agent,
            depth=depth,
            name=name,
            role=role,
            running=running,
            status=status,
            subagent_id=subagent_id,
            usage=usage,
        )

        subagent_info.additional_properties = d
        return subagent_info

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
