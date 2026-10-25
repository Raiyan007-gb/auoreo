from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="MemoryFile")


@_attrs_define
class MemoryFile:
    """
    Attributes:
        exists (bool):
        name (str):
        content (str | Unset): Markdown with secrets redacted Default: ''.
        size (int | Unset):  Default: 0.
    """

    exists: bool
    name: str
    content: str | Unset = ""
    size: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        exists = self.exists

        name = self.name

        content = self.content

        size = self.size

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "exists": exists,
                "name": name,
            }
        )
        if content is not UNSET:
            field_dict["content"] = content
        if size is not UNSET:
            field_dict["size"] = size

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        exists = d.pop("exists")

        name = d.pop("name")

        content = d.pop("content", UNSET)

        size = d.pop("size", UNSET)

        memory_file = cls(
            exists=exists,
            name=name,
            content=content,
            size=size,
        )

        memory_file.additional_properties = d
        return memory_file

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
