from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UsagePoint")


@_attrs_define
class UsagePoint:
    """
    Attributes:
        bucket (datetime.datetime): Start of the bucket (UTC)
        calls (int):
        completion_tokens (int):
        cost_usd (float):
        prompt_tokens (int):
        cache_write_tokens (int | Unset):  Default: 0.
        cached_tokens (int | Unset):  Default: 0.
    """

    bucket: datetime.datetime
    calls: int
    completion_tokens: int
    cost_usd: float
    prompt_tokens: int
    cache_write_tokens: int | Unset = 0
    cached_tokens: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bucket = self.bucket.isoformat()

        calls = self.calls

        completion_tokens = self.completion_tokens

        cost_usd = self.cost_usd

        prompt_tokens = self.prompt_tokens

        cache_write_tokens = self.cache_write_tokens

        cached_tokens = self.cached_tokens

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "bucket": bucket,
                "calls": calls,
                "completion_tokens": completion_tokens,
                "cost_usd": cost_usd,
                "prompt_tokens": prompt_tokens,
            }
        )
        if cache_write_tokens is not UNSET:
            field_dict["cache_write_tokens"] = cache_write_tokens
        if cached_tokens is not UNSET:
            field_dict["cached_tokens"] = cached_tokens

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        bucket = datetime.datetime.fromisoformat(d.pop("bucket"))

        calls = d.pop("calls")

        completion_tokens = d.pop("completion_tokens")

        cost_usd = d.pop("cost_usd")

        prompt_tokens = d.pop("prompt_tokens")

        cache_write_tokens = d.pop("cache_write_tokens", UNSET)

        cached_tokens = d.pop("cached_tokens", UNSET)

        usage_point = cls(
            bucket=bucket,
            calls=calls,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            prompt_tokens=prompt_tokens,
            cache_write_tokens=cache_write_tokens,
            cached_tokens=cached_tokens,
        )

        usage_point.additional_properties = d
        return usage_point

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
