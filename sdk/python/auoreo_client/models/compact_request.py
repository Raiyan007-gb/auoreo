from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="CompactRequest")


@_attrs_define
class CompactRequest:
    """
    Attributes:
        retain_ratio (float | None | Unset):
    """

    retain_ratio: float | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        retain_ratio: float | None | Unset
        if isinstance(self.retain_ratio, Unset):
            retain_ratio = UNSET
        else:
            retain_ratio = self.retain_ratio

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if retain_ratio is not UNSET:
            field_dict["retain_ratio"] = retain_ratio

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_retain_ratio(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        retain_ratio = _parse_retain_ratio(d.pop("retain_ratio", UNSET))

        compact_request = cls(
            retain_ratio=retain_ratio,
        )

        return compact_request
