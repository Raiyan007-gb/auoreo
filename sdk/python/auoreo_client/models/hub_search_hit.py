from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.hub_search_hit_installed_type_0 import HubSearchHitInstalledType0
from ..types import UNSET, Unset

T = TypeVar("T", bound="HubSearchHit")


@_attrs_define
class HubSearchHit:
    """
    Attributes:
        description (str):
        name (str):
        tap (str):
        installed (HubSearchHitInstalledType0 | None | Unset):
        version (str | Unset):  Default: ''.
    """

    description: str
    name: str
    tap: str
    installed: HubSearchHitInstalledType0 | None | Unset = UNSET
    version: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        name = self.name

        tap = self.tap

        installed: None | str | Unset
        if isinstance(self.installed, Unset):
            installed = UNSET
        elif isinstance(self.installed, HubSearchHitInstalledType0):
            installed = self.installed.value
        else:
            installed = self.installed

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "name": name,
                "tap": tap,
            }
        )
        if installed is not UNSET:
            field_dict["installed"] = installed
        if version is not UNSET:
            field_dict["version"] = version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        description = d.pop("description")

        name = d.pop("name")

        tap = d.pop("tap")

        def _parse_installed(data: object) -> HubSearchHitInstalledType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                installed_type_0 = HubSearchHitInstalledType0(data)

                return installed_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(HubSearchHitInstalledType0 | None | Unset, data)

        installed = _parse_installed(d.pop("installed", UNSET))

        version = d.pop("version", UNSET)

        hub_search_hit = cls(
            description=description,
            name=name,
            tap=tap,
            installed=installed,
            version=version,
        )

        hub_search_hit.additional_properties = d
        return hub_search_hit

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
