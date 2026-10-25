from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.usage_totals import UsageTotals


T = TypeVar("T", bound="UsageSummary")


@_attrs_define
class UsageSummary:
    """
    Attributes:
        by_model (list[UsageTotals]):
        by_session (list[UsageTotals]):
        days (int):
        total (UsageTotals):
    """

    by_model: list[UsageTotals]
    by_session: list[UsageTotals]
    days: int
    total: UsageTotals
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        by_model = []
        for by_model_item_data in self.by_model:
            by_model_item = by_model_item_data.to_dict()
            by_model.append(by_model_item)

        by_session = []
        for by_session_item_data in self.by_session:
            by_session_item = by_session_item_data.to_dict()
            by_session.append(by_session_item)

        days = self.days

        total = self.total.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "by_model": by_model,
                "by_session": by_session,
                "days": days,
                "total": total,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.usage_totals import UsageTotals  # noqa: PLC0415

        d = dict(src_dict)
        by_model = []
        _by_model = d.pop("by_model")
        for by_model_item_data in _by_model:
            by_model_item = UsageTotals.from_dict(by_model_item_data)

            by_model.append(by_model_item)

        by_session = []
        _by_session = d.pop("by_session")
        for by_session_item_data in _by_session:
            by_session_item = UsageTotals.from_dict(by_session_item_data)

            by_session.append(by_session_item)

        days = d.pop("days")

        total = UsageTotals.from_dict(d.pop("total"))

        usage_summary = cls(
            by_model=by_model,
            by_session=by_session,
            days=days,
            total=total,
        )

        usage_summary.additional_properties = d
        return usage_summary

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
