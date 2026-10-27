from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.dotenv_import_counts import DotenvImportCounts
    from ..models.dotenv_move import DotenvMove
    from ..models.dotenv_provider import DotenvProvider


T = TypeVar("T", bound="DotenvImport")


@_attrs_define
class DotenvImport:
    """
    Attributes:
        counts (DotenvImportCounts):
        dry_run (bool):
        moved (list[DotenvMove]):
        note (str):
        files (list[str] | Unset):
        providers (list[DotenvProvider] | Unset):
    """

    counts: DotenvImportCounts
    dry_run: bool
    moved: list[DotenvMove]
    note: str
    files: list[str] | Unset = UNSET
    providers: list[DotenvProvider] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        counts = self.counts.to_dict()

        dry_run = self.dry_run

        moved = []
        for moved_item_data in self.moved:
            moved_item = moved_item_data.to_dict()
            moved.append(moved_item)

        note = self.note

        files: list[str] | Unset = UNSET
        if not isinstance(self.files, Unset):
            files = self.files

        providers: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.providers, Unset):
            providers = []
            for providers_item_data in self.providers:
                providers_item = providers_item_data.to_dict()
                providers.append(providers_item)

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
        if files is not UNSET:
            field_dict["files"] = files
        if providers is not UNSET:
            field_dict["providers"] = providers

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.dotenv_import_counts import DotenvImportCounts  # noqa: PLC0415
        from ..models.dotenv_move import DotenvMove  # noqa: PLC0415
        from ..models.dotenv_provider import DotenvProvider  # noqa: PLC0415

        d = dict(src_dict)
        counts = DotenvImportCounts.from_dict(d.pop("counts"))

        dry_run = d.pop("dry_run")

        moved = []
        _moved = d.pop("moved")
        for moved_item_data in _moved:
            moved_item = DotenvMove.from_dict(moved_item_data)

            moved.append(moved_item)

        note = d.pop("note")

        files = cast(list[str], d.pop("files", UNSET))

        _providers = d.pop("providers", UNSET)
        providers: list[DotenvProvider] | Unset = UNSET
        if _providers is not UNSET:
            providers = []
            for providers_item_data in _providers:
                providers_item = DotenvProvider.from_dict(providers_item_data)

                providers.append(providers_item)

        dotenv_import = cls(
            counts=counts,
            dry_run=dry_run,
            moved=moved,
            note=note,
            files=files,
            providers=providers,
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
