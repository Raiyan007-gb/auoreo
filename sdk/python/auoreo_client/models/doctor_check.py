from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.doctor_check_level import DoctorCheckLevel
from ..types import UNSET, Unset

T = TypeVar("T", bound="DoctorCheck")


@_attrs_define
class DoctorCheck:
    """
    Attributes:
        check (str): Check name, e.g. `python`, `backend:stt`, `credentials`
        detail (str):
        level (DoctorCheckLevel):
        remedy (str | Unset):  Default: ''.
    """

    check: str
    detail: str
    level: DoctorCheckLevel
    remedy: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        check = self.check

        detail = self.detail

        level = self.level.value

        remedy = self.remedy

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "check": check,
                "detail": detail,
                "level": level,
            }
        )
        if remedy is not UNSET:
            field_dict["remedy"] = remedy

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        check = d.pop("check")

        detail = d.pop("detail")

        level = DoctorCheckLevel(d.pop("level"))

        remedy = d.pop("remedy", UNSET)

        doctor_check = cls(
            check=check,
            detail=detail,
            level=level,
            remedy=remedy,
        )

        doctor_check.additional_properties = d
        return doctor_check

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
