from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="RevertResult")


@_attrs_define
class RevertResult:
    """
    Attributes:
        removed (int):
        session_id (str):
        to_idx (int):
    """

    removed: int
    session_id: str
    to_idx: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        removed = self.removed

        session_id = self.session_id

        to_idx = self.to_idx

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "removed": removed,
                "session_id": session_id,
                "to_idx": to_idx,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        removed = d.pop("removed")

        session_id = d.pop("session_id")

        to_idx = d.pop("to_idx")

        revert_result = cls(
            removed=removed,
            session_id=session_id,
            to_idx=to_idx,
        )

        revert_result.additional_properties = d
        return revert_result

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
