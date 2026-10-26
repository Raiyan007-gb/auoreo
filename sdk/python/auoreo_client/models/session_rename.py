from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="SessionRename")


@_attrs_define
class SessionRename:
    """
    Attributes:
        title (str):
    """

    title: str

    def to_dict(self) -> dict[str, Any]:
        title = self.title

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "title": title,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        title = d.pop("title")

        session_rename = cls(
            title=title,
        )

        return session_rename
