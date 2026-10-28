from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.doctor_fix import DoctorFix


T = TypeVar("T", bound="DoctorFixPlan")


@_attrs_define
class DoctorFixPlan:
    """
    Attributes:
        fixes (list[DoctorFix]):
    """

    fixes: list[DoctorFix]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        fixes = []
        for fixes_item_data in self.fixes:
            fixes_item = fixes_item_data.to_dict()
            fixes.append(fixes_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "fixes": fixes,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.doctor_fix import DoctorFix  # noqa: PLC0415

        d = dict(src_dict)
        fixes = []
        _fixes = d.pop("fixes")
        for fixes_item_data in _fixes:
            fixes_item = DoctorFix.from_dict(fixes_item_data)

            fixes.append(fixes_item)

        doctor_fix_plan = cls(
            fixes=fixes,
        )

        doctor_fix_plan.additional_properties = d
        return doctor_fix_plan

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
