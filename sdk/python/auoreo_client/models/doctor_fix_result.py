from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.doctor_fix import DoctorFix
    from ..models.doctor_fix_failure import DoctorFixFailure


T = TypeVar("T", bound="DoctorFixResult")


@_attrs_define
class DoctorFixResult:
    """
    Attributes:
        applied (list[DoctorFix]):
        failed (list[DoctorFixFailure]):
    """

    applied: list[DoctorFix]
    failed: list[DoctorFixFailure]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        applied = []
        for applied_item_data in self.applied:
            applied_item = applied_item_data.to_dict()
            applied.append(applied_item)

        failed = []
        for failed_item_data in self.failed:
            failed_item = failed_item_data.to_dict()
            failed.append(failed_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "applied": applied,
                "failed": failed,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.doctor_fix import DoctorFix  # noqa: PLC0415
        from ..models.doctor_fix_failure import DoctorFixFailure  # noqa: PLC0415

        d = dict(src_dict)
        applied = []
        _applied = d.pop("applied")
        for applied_item_data in _applied:
            applied_item = DoctorFix.from_dict(applied_item_data)

            applied.append(applied_item)

        failed = []
        _failed = d.pop("failed")
        for failed_item_data in _failed:
            failed_item = DoctorFixFailure.from_dict(failed_item_data)

            failed.append(failed_item)

        doctor_fix_result = cls(
            applied=applied,
            failed=failed,
        )

        doctor_fix_result.additional_properties = d
        return doctor_fix_result

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
