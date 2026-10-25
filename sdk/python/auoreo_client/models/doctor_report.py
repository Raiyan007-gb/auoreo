from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.doctor_check import DoctorCheck
    from ..models.doctor_summary import DoctorSummary


T = TypeVar("T", bound="DoctorReport")


@_attrs_define
class DoctorReport:
    """Read-only environment report: never repairs, never includes secret values.

    Attributes:
        checks (list[DoctorCheck]):
        summary (DoctorSummary):
    """

    checks: list[DoctorCheck]
    summary: DoctorSummary
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        checks = []
        for checks_item_data in self.checks:
            checks_item = checks_item_data.to_dict()
            checks.append(checks_item)

        summary = self.summary.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "checks": checks,
                "summary": summary,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.doctor_check import DoctorCheck  # noqa: PLC0415
        from ..models.doctor_summary import DoctorSummary  # noqa: PLC0415

        d = dict(src_dict)
        checks = []
        _checks = d.pop("checks")
        for checks_item_data in _checks:
            checks_item = DoctorCheck.from_dict(checks_item_data)

            checks.append(checks_item)

        summary = DoctorSummary.from_dict(d.pop("summary"))

        doctor_report = cls(
            checks=checks,
            summary=summary,
        )

        doctor_report.additional_properties = d
        return doctor_report

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
