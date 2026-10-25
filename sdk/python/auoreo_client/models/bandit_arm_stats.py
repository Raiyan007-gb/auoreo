from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.bandit_arm_stats_effort_type_0 import BanditArmStatsEffortType0
from ..types import UNSET, Unset

T = TypeVar("T", bound="BanditArmStats")


@_attrs_define
class BanditArmStats:
    """
    Attributes:
        context_key (str):
        key (str):
        mean_reward (float):
        model (str):
        pulls (int):
        updated_at (datetime.datetime):
        effort (BanditArmStatsEffortType0 | None | Unset):
        top_k (int | None | Unset):
    """

    context_key: str
    key: str
    mean_reward: float
    model: str
    pulls: int
    updated_at: datetime.datetime
    effort: BanditArmStatsEffortType0 | None | Unset = UNSET
    top_k: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        context_key = self.context_key

        key = self.key

        mean_reward = self.mean_reward

        model = self.model

        pulls = self.pulls

        updated_at = self.updated_at.isoformat()

        effort: None | str | Unset
        if isinstance(self.effort, Unset):
            effort = UNSET
        elif isinstance(self.effort, BanditArmStatsEffortType0):
            effort = self.effort.value
        else:
            effort = self.effort

        top_k: int | None | Unset
        if isinstance(self.top_k, Unset):
            top_k = UNSET
        else:
            top_k = self.top_k

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "context_key": context_key,
                "key": key,
                "mean_reward": mean_reward,
                "model": model,
                "pulls": pulls,
                "updated_at": updated_at,
            }
        )
        if effort is not UNSET:
            field_dict["effort"] = effort
        if top_k is not UNSET:
            field_dict["top_k"] = top_k

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        context_key = d.pop("context_key")

        key = d.pop("key")

        mean_reward = d.pop("mean_reward")

        model = d.pop("model")

        pulls = d.pop("pulls")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        def _parse_effort(data: object) -> BanditArmStatsEffortType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                effort_type_0 = BanditArmStatsEffortType0(data)

                return effort_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BanditArmStatsEffortType0 | None | Unset, data)

        effort = _parse_effort(d.pop("effort", UNSET))

        def _parse_top_k(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        top_k = _parse_top_k(d.pop("top_k", UNSET))

        bandit_arm_stats = cls(
            context_key=context_key,
            key=key,
            mean_reward=mean_reward,
            model=model,
            pulls=pulls,
            updated_at=updated_at,
            effort=effort,
            top_k=top_k,
        )

        bandit_arm_stats.additional_properties = d
        return bandit_arm_stats

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
