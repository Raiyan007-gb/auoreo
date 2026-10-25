from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="BanditContextInfo")


@_attrs_define
class BanditContextInfo:
    """
    Attributes:
        context_key (str):
        epsilon (float): Exploration rate the next choice in this context uses.
        pulls (int):
    """

    context_key: str
    epsilon: float
    pulls: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        context_key = self.context_key

        epsilon = self.epsilon

        pulls = self.pulls

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "context_key": context_key,
                "epsilon": epsilon,
                "pulls": pulls,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        context_key = d.pop("context_key")

        epsilon = d.pop("epsilon")

        pulls = d.pop("pulls")

        bandit_context_info = cls(
            context_key=context_key,
            epsilon=epsilon,
            pulls=pulls,
        )

        bandit_context_info.additional_properties = d
        return bandit_context_info

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
