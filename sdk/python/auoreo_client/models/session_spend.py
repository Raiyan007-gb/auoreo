from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SessionSpend")


@_attrs_define
class SessionSpend:
    """
    Attributes:
        cost_usd (float):
        session_id (str):
        blocked (bool | Unset):  Default: False.
        ratio (float | None | Unset): cost / max_cost_usd (None without a cap)
    """

    cost_usd: float
    session_id: str
    blocked: bool | Unset = False
    ratio: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cost_usd = self.cost_usd

        session_id = self.session_id

        blocked = self.blocked

        ratio: float | None | Unset
        if isinstance(self.ratio, Unset):
            ratio = UNSET
        else:
            ratio = self.ratio

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "cost_usd": cost_usd,
                "session_id": session_id,
            }
        )
        if blocked is not UNSET:
            field_dict["blocked"] = blocked
        if ratio is not UNSET:
            field_dict["ratio"] = ratio

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        cost_usd = d.pop("cost_usd")

        session_id = d.pop("session_id")

        blocked = d.pop("blocked", UNSET)

        def _parse_ratio(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        ratio = _parse_ratio(d.pop("ratio", UNSET))

        session_spend = cls(
            cost_usd=cost_usd,
            session_id=session_id,
            blocked=blocked,
            ratio=ratio,
        )

        session_spend.additional_properties = d
        return session_spend

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
