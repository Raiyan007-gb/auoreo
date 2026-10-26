from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="HarnessEntry")


@_attrs_define
class HarnessEntry:
    """
    Attributes:
        description (str):
        kind (str):
        name (str):
        path (None | str):
        source (str):
        available (bool | None | Unset):
        emoji (None | str | Unset):
        unavailable_reason (None | str | Unset):
    """

    description: str
    kind: str
    name: str
    path: None | str
    source: str
    available: bool | None | Unset = UNSET
    emoji: None | str | Unset = UNSET
    unavailable_reason: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        kind = self.kind

        name = self.name

        path: None | str
        path = self.path

        source = self.source

        available: bool | None | Unset
        if isinstance(self.available, Unset):
            available = UNSET
        else:
            available = self.available

        emoji: None | str | Unset
        if isinstance(self.emoji, Unset):
            emoji = UNSET
        else:
            emoji = self.emoji

        unavailable_reason: None | str | Unset
        if isinstance(self.unavailable_reason, Unset):
            unavailable_reason = UNSET
        else:
            unavailable_reason = self.unavailable_reason

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "kind": kind,
                "name": name,
                "path": path,
                "source": source,
            }
        )
        if available is not UNSET:
            field_dict["available"] = available
        if emoji is not UNSET:
            field_dict["emoji"] = emoji
        if unavailable_reason is not UNSET:
            field_dict["unavailable_reason"] = unavailable_reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        description = d.pop("description")

        kind = d.pop("kind")

        name = d.pop("name")

        def _parse_path(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        path = _parse_path(d.pop("path"))

        source = d.pop("source")

        def _parse_available(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        available = _parse_available(d.pop("available", UNSET))

        def _parse_emoji(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        emoji = _parse_emoji(d.pop("emoji", UNSET))

        def _parse_unavailable_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        unavailable_reason = _parse_unavailable_reason(d.pop("unavailable_reason", UNSET))

        harness_entry = cls(
            description=description,
            kind=kind,
            name=name,
            path=path,
            source=source,
            available=available,
            emoji=emoji,
            unavailable_reason=unavailable_reason,
        )

        harness_entry.additional_properties = d
        return harness_entry

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
