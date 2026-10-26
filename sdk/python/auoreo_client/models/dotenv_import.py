from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.dotenv_import_counts import DotenvImportCounts
    from ..models.dotenv_move import DotenvMove


T = TypeVar("T", bound="DotenvImport")


@_attrs_define
class DotenvImport:
    """
    Attributes:
        counts (DotenvImportCounts):
        dry_run (bool):
        moved (list[DotenvMove]):
        note (str):
    """

    counts: DotenvImportCounts
    dry_run: bool
    moved: list[DotenvMove]
    note: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        counts = self.counts.to_dict()

        dry_run = self.dry_run

        moved = []
        for moved_item_data in self.moved:
            moved_item = moved_item_data.to_dict()
            moved.append(moved_item)

        note = self.note

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "counts": counts,
                "dry_run": dry_run,
                "moved": moved,
                "note": note,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.dotenv_import_counts import DotenvImportCounts  # noqa: PLC0415
        from ..models.dotenv_move import DotenvMove  # noqa: PLC0415

        d = dict(src_dict)
        counts = DotenvImportCounts.from_dict(d.pop("counts"))

        dry_run = d.pop("dry_run")

        moved = []
        _moved = d.pop("moved")
        for moved_item_data in _moved:
            moved_item = DotenvMove.from_dict(moved_item_data)

            moved.append(moved_item)

        note = d.pop("note")

        dotenv_import = cls(
            counts=counts,
            dry_run=dry_run,
            moved=moved,
            note=note,
        )

        dotenv_import.additional_properties = d
        return dotenv_import

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
