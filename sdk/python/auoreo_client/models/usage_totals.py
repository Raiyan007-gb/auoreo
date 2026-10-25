from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="UsageTotals")


@_attrs_define
class UsageTotals:
    """
    Attributes:
        calls (int):
        completion_tokens (int):
        cost_usd (float):
        key (str):
        prompt_tokens (int):
    """

    calls: int
    completion_tokens: int
    cost_usd: float
    key: str
    prompt_tokens: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        calls = self.calls

        completion_tokens = self.completion_tokens

        cost_usd = self.cost_usd

        key = self.key

        prompt_tokens = self.prompt_tokens

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "calls": calls,
                "completion_tokens": completion_tokens,
                "cost_usd": cost_usd,
                "key": key,
                "prompt_tokens": prompt_tokens,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        calls = d.pop("calls")

        completion_tokens = d.pop("completion_tokens")

        cost_usd = d.pop("cost_usd")

        key = d.pop("key")

        prompt_tokens = d.pop("prompt_tokens")

        usage_totals = cls(
            calls=calls,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            key=key,
            prompt_tokens=prompt_tokens,
        )

        usage_totals.additional_properties = d
        return usage_totals

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
