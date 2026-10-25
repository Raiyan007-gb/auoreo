from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="FactPin")


@_attrs_define
class FactPin:
    """
    Attributes:
        pinned (bool | Unset):  Default: True.
    """

    pinned: bool | Unset = True

    def to_dict(self) -> dict[str, Any]:
        pinned = self.pinned

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if pinned is not UNSET:
            field_dict["pinned"] = pinned

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        pinned = d.pop("pinned", UNSET)

        fact_pin = cls(
            pinned=pinned,
        )

        return fact_pin
