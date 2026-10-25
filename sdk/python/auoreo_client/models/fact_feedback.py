from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="FactFeedback")


@_attrs_define
class FactFeedback:
    """
    Attributes:
        helpful (bool):
    """

    helpful: bool

    def to_dict(self) -> dict[str, Any]:
        helpful = self.helpful

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "helpful": helpful,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        helpful = d.pop("helpful")

        fact_feedback = cls(
            helpful=helpful,
        )

        return fact_feedback
