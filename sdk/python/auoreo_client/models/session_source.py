from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.session_source_kind import SessionSourceKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="SessionSource")


@_attrs_define
class SessionSource:
    """
    Attributes:
        enabled (bool): Enabled for this session (sources are on unless disabled here)
        id (str): `mcp:<server>` | `plugin:<name>`
        kind (SessionSourceKind):
        name (str):
        status (str): Global state: connected/running/disabled/…
        skills (list[str] | Unset):
        tools (int | Unset):  Default: 0.
    """

    enabled: bool
    id: str
    kind: SessionSourceKind
    name: str
    status: str
    skills: list[str] | Unset = UNSET
    tools: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        enabled = self.enabled

        id = self.id

        kind = self.kind.value

        name = self.name

        status = self.status

        skills: list[str] | Unset = UNSET
        if not isinstance(self.skills, Unset):
            skills = self.skills

        tools = self.tools

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "enabled": enabled,
                "id": id,
                "kind": kind,
                "name": name,
                "status": status,
            }
        )
        if skills is not UNSET:
            field_dict["skills"] = skills
        if tools is not UNSET:
            field_dict["tools"] = tools

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        enabled = d.pop("enabled")

        id = d.pop("id")

        kind = SessionSourceKind(d.pop("kind"))

        name = d.pop("name")

        status = d.pop("status")

        skills = cast(list[str], d.pop("skills", UNSET))

        tools = d.pop("tools", UNSET)

        session_source = cls(
            enabled=enabled,
            id=id,
            kind=kind,
            name=name,
            status=status,
            skills=skills,
            tools=tools,
        )

        session_source.additional_properties = d
        return session_source

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
