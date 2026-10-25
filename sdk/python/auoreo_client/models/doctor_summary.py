from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="DoctorSummary")


@_attrs_define
class DoctorSummary:
    """
    Attributes:
        error (int | Unset):  Default: 0.
        ok (int | Unset):  Default: 0.
        warn (int | Unset):  Default: 0.
    """

    error: int | Unset = 0
    ok: int | Unset = 0
    warn: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        error = self.error

        ok = self.ok

        warn = self.warn

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if error is not UNSET:
            field_dict["error"] = error
        if ok is not UNSET:
            field_dict["ok"] = ok
        if warn is not UNSET:
            field_dict["warn"] = warn

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        error = d.pop("error", UNSET)

        ok = d.pop("ok", UNSET)

        warn = d.pop("warn", UNSET)

        doctor_summary = cls(
            error=error,
            ok=ok,
            warn=warn,
        )

        doctor_summary.additional_properties = d
        return doctor_summary

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
