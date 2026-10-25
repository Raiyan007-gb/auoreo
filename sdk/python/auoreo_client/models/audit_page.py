from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.audit_entry import AuditEntry


T = TypeVar("T", bound="AuditPage")


@_attrs_define
class AuditPage:
    """
    Attributes:
        rows (list[AuditEntry]):
        next_after_seq (int | None | Unset): Pass as `after_seq` for the next page; null at the end
    """

    rows: list[AuditEntry]
    next_after_seq: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        rows = []
        for rows_item_data in self.rows:
            rows_item = rows_item_data.to_dict()
            rows.append(rows_item)

        next_after_seq: int | None | Unset
        if isinstance(self.next_after_seq, Unset):
            next_after_seq = UNSET
        else:
            next_after_seq = self.next_after_seq

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "rows": rows,
            }
        )
        if next_after_seq is not UNSET:
            field_dict["next_after_seq"] = next_after_seq

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.audit_entry import AuditEntry  # noqa: PLC0415

        d = dict(src_dict)
        rows = []
        _rows = d.pop("rows")
        for rows_item_data in _rows:
            rows_item = AuditEntry.from_dict(rows_item_data)

            rows.append(rows_item)

        def _parse_next_after_seq(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        next_after_seq = _parse_next_after_seq(d.pop("next_after_seq", UNSET))

        audit_page = cls(
            rows=rows,
            next_after_seq=next_after_seq,
        )

        audit_page.additional_properties = d
        return audit_page

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
