from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="RevertRequest")


@_attrs_define
class RevertRequest:
    """
    Attributes:
        to_idx (int): Keep messages 0..to_idx (-1 clears the history)
    """

    to_idx: int

    def to_dict(self) -> dict[str, Any]:
        to_idx = self.to_idx

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "to_idx": to_idx,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        to_idx = d.pop("to_idx")

        revert_request = cls(
            to_idx=to_idx,
        )

        return revert_request
