from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="PluginPair")


@_attrs_define
class PluginPair:
    """
    Attributes:
        code (str): 6-digit pairing code shown to the remote chat
    """

    code: str

    def to_dict(self) -> dict[str, Any]:
        code = self.code

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "code": code,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        code = d.pop("code")

        plugin_pair = cls(
            code=code,
        )

        return plugin_pair
