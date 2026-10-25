from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.harness_listing_entries import HarnessListingEntries


T = TypeVar("T", bound="HarnessListing")


@_attrs_define
class HarnessListing:
    """
    Attributes:
        entries (HarnessListingEntries):
        errors (list[str]):
        generation (int):
    """

    entries: HarnessListingEntries
    errors: list[str]
    generation: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        entries = self.entries.to_dict()

        errors = self.errors

        generation = self.generation

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "entries": entries,
                "errors": errors,
                "generation": generation,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.harness_listing_entries import HarnessListingEntries  # noqa: PLC0415

        d = dict(src_dict)
        entries = HarnessListingEntries.from_dict(d.pop("entries"))

        errors = cast(list[str], d.pop("errors"))

        generation = d.pop("generation")

        harness_listing = cls(
            entries=entries,
            errors=errors,
            generation=generation,
        )

        harness_listing.additional_properties = d
        return harness_listing

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
