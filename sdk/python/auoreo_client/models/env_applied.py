from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="EnvApplied")


@_attrs_define
class EnvApplied:
    """What a write changed and how it applies.

    Attributes:
        action (str):
        note (str):
        changed (list[str] | Unset):
        live (list[str] | Unset):
        name (None | str | Unset):
        restart_required (list[str] | Unset):
    """

    action: str
    note: str
    changed: list[str] | Unset = UNSET
    live: list[str] | Unset = UNSET
    name: None | str | Unset = UNSET
    restart_required: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        action = self.action

        note = self.note

        changed: list[str] | Unset = UNSET
        if not isinstance(self.changed, Unset):
            changed = self.changed

        live: list[str] | Unset = UNSET
        if not isinstance(self.live, Unset):
            live = self.live

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        restart_required: list[str] | Unset = UNSET
        if not isinstance(self.restart_required, Unset):
            restart_required = self.restart_required

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "action": action,
                "note": note,
            }
        )
        if changed is not UNSET:
            field_dict["changed"] = changed
        if live is not UNSET:
            field_dict["live"] = live
        if name is not UNSET:
            field_dict["name"] = name
        if restart_required is not UNSET:
            field_dict["restart_required"] = restart_required

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        action = d.pop("action")

        note = d.pop("note")

        changed = cast(list[str], d.pop("changed", UNSET))

        live = cast(list[str], d.pop("live", UNSET))

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        restart_required = cast(list[str], d.pop("restart_required", UNSET))

        env_applied = cls(
            action=action,
            note=note,
            changed=changed,
            live=live,
            name=name,
            restart_required=restart_required,
        )

        env_applied.additional_properties = d
        return env_applied

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
