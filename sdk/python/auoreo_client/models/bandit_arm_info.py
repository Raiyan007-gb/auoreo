from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.bandit_arm_info_effort_type_0 import BanditArmInfoEffortType0
from ..types import UNSET, Unset

T = TypeVar("T", bound="BanditArmInfo")


@_attrs_define
class BanditArmInfo:
    """One configured arm (`settings.bandit.arms`, or derived from the routing groups).

    Attributes:
        key (str):
        model (str):
        effort (BanditArmInfoEffortType0 | None | Unset):
        top_k (int | None | Unset):
    """

    key: str
    model: str
    effort: BanditArmInfoEffortType0 | None | Unset = UNSET
    top_k: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        key = self.key

        model = self.model

        effort: None | str | Unset
        if isinstance(self.effort, Unset):
            effort = UNSET
        elif isinstance(self.effort, BanditArmInfoEffortType0):
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
                "key": key,
                "model": model,
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
        key = d.pop("key")

        model = d.pop("model")

        def _parse_effort(data: object) -> BanditArmInfoEffortType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                effort_type_0 = BanditArmInfoEffortType0(data)

                return effort_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BanditArmInfoEffortType0 | None | Unset, data)

        effort = _parse_effort(d.pop("effort", UNSET))

        def _parse_top_k(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        top_k = _parse_top_k(d.pop("top_k", UNSET))

        bandit_arm_info = cls(
            key=key,
            model=model,
            effort=effort,
            top_k=top_k,
        )

        bandit_arm_info.additional_properties = d
        return bandit_arm_info

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
