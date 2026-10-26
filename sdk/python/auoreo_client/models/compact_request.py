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
        from_ (int | None | Unset):
        retain_ratio (float | None | Unset):
        up_to (int | None | Unset):
    """

    from_: int | None | Unset = UNSET
    retain_ratio: float | None | Unset = UNSET
    up_to: int | None | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from_: int | None | Unset
        if isinstance(self.from_, Unset):
            from_ = UNSET
        else:
            from_ = self.from_

        retain_ratio: float | None | Unset
        if isinstance(self.retain_ratio, Unset):
            retain_ratio = UNSET
        else:
            retain_ratio = self.retain_ratio

        up_to: int | None | Unset
        if isinstance(self.up_to, Unset):
            up_to = UNSET
        else:
            up_to = self.up_to

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if from_ is not UNSET:
            field_dict["from"] = from_
        if retain_ratio is not UNSET:
            field_dict["retain_ratio"] = retain_ratio
        if up_to is not UNSET:
            field_dict["up_to"] = up_to

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_from_(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        from_ = _parse_from_(d.pop("from", UNSET))

        def _parse_retain_ratio(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        retain_ratio = _parse_retain_ratio(d.pop("retain_ratio", UNSET))

        def _parse_up_to(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        up_to = _parse_up_to(d.pop("up_to", UNSET))

        compact_request = cls(
            from_=from_,
            retain_ratio=retain_ratio,
            up_to=up_to,
        )

        return compact_request
