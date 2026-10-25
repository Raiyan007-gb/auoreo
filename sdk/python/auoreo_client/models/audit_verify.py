from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="AuditVerify")


@_attrs_define
class AuditVerify:
    """
    Attributes:
        ok (bool):
        rows (int): Total audit rows checked
        first_break_seq (int | None | Unset):
    """

    ok: bool
    rows: int
    first_break_seq: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        ok = self.ok

        rows = self.rows

        first_break_seq: int | None | Unset
        if isinstance(self.first_break_seq, Unset):
            first_break_seq = UNSET
        else:
            first_break_seq = self.first_break_seq

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "ok": ok,
                "rows": rows,
            }
        )
        if first_break_seq is not UNSET:
            field_dict["first_break_seq"] = first_break_seq

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        ok = d.pop("ok")

        rows = d.pop("rows")

        def _parse_first_break_seq(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        first_break_seq = _parse_first_break_seq(d.pop("first_break_seq", UNSET))

        audit_verify = cls(
            ok=ok,
            rows=rows,
            first_break_seq=first_break_seq,
        )

        audit_verify.additional_properties = d
        return audit_verify

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
