from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.doctor_fix_kind import DoctorFixKind

T = TypeVar("T", bound="DoctorFix")


@_attrs_define
class DoctorFix:
    """One safe, local repair (`auoreo doctor --fix`): never settings, secrets or user data.

    Attributes:
        detail (str):
        kind (DoctorFixKind):
        target (str): The directory, file or worktree it touches
    """

    detail: str
    kind: DoctorFixKind
    target: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        detail = self.detail

        kind = self.kind.value

        target = self.target

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "detail": detail,
                "kind": kind,
                "target": target,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        detail = d.pop("detail")

        kind = DoctorFixKind(d.pop("kind"))

        target = d.pop("target")

        doctor_fix = cls(
            detail=detail,
            kind=kind,
            target=target,
        )

        doctor_fix.additional_properties = d
        return doctor_fix

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
