from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.hub_removed_status import HubRemovedStatus

T = TypeVar("T", bound="HubRemoved")


@_attrs_define
class HubRemoved:
    """
    Attributes:
        name (str):
        removed (bool):
        status (HubRemovedStatus):
    """

    name: str
    removed: bool
    status: HubRemovedStatus
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        removed = self.removed

        status = self.status.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "removed": removed,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        removed = d.pop("removed")

        status = HubRemovedStatus(d.pop("status"))

        hub_removed = cls(
            name=name,
            removed=removed,
            status=status,
        )

        hub_removed.additional_properties = d
        return hub_removed

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
