from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="UsageBudget")


@_attrs_define
class UsageBudget:
    """Budget guards from settings: per-session spend cap and per-turn limits.

    Attributes:
        max_cost_usd (float | None | Unset):
        run_budget_seconds (float | None | Unset):
        token_budget (int | None | Unset):
    """

    max_cost_usd: float | None | Unset = UNSET
    run_budget_seconds: float | None | Unset = UNSET
    token_budget: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        max_cost_usd: float | None | Unset
        if isinstance(self.max_cost_usd, Unset):
            max_cost_usd = UNSET
        else:
            max_cost_usd = self.max_cost_usd

        run_budget_seconds: float | None | Unset
        if isinstance(self.run_budget_seconds, Unset):
            run_budget_seconds = UNSET
        else:
            run_budget_seconds = self.run_budget_seconds

        token_budget: int | None | Unset
        if isinstance(self.token_budget, Unset):
            token_budget = UNSET
        else:
            token_budget = self.token_budget

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if max_cost_usd is not UNSET:
            field_dict["max_cost_usd"] = max_cost_usd
        if run_budget_seconds is not UNSET:
            field_dict["run_budget_seconds"] = run_budget_seconds
        if token_budget is not UNSET:
            field_dict["token_budget"] = token_budget

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_max_cost_usd(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        max_cost_usd = _parse_max_cost_usd(d.pop("max_cost_usd", UNSET))

        def _parse_run_budget_seconds(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        run_budget_seconds = _parse_run_budget_seconds(d.pop("run_budget_seconds", UNSET))

        def _parse_token_budget(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        token_budget = _parse_token_budget(d.pop("token_budget", UNSET))

        usage_budget = cls(
            max_cost_usd=max_cost_usd,
            run_budget_seconds=run_budget_seconds,
            token_budget=token_budget,
        )

        usage_budget.additional_properties = d
        return usage_budget

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
