from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.suggestion_info import SuggestionInfo


T = TypeVar("T", bound="SuggestionProposed")


@_attrs_define
class SuggestionProposed:
    """
    Attributes:
        created (bool): False when a pending suggestion with the same dedupe_key existed
        suggestion (SuggestionInfo):
    """

    created: bool
    suggestion: SuggestionInfo
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created = self.created

        suggestion = self.suggestion.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created": created,
                "suggestion": suggestion,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.suggestion_info import SuggestionInfo  # noqa: PLC0415

        d = dict(src_dict)
        created = d.pop("created")

        suggestion = SuggestionInfo.from_dict(d.pop("suggestion"))

        suggestion_proposed = cls(
            created=created,
            suggestion=suggestion,
        )

        suggestion_proposed.additional_properties = d
        return suggestion_proposed

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
