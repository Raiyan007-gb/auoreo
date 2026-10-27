from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.provider_spec_out import ProviderSpecOut


T = TypeVar("T", bound="ProviderDetectResult")


@_attrs_define
class ProviderDetectResult:
    """
    Attributes:
        candidates (list[str]):
        confident (bool):
        providers (list[ProviderSpecOut]): Specs of the candidates, in order
    """

    candidates: list[str]
    confident: bool
    providers: list[ProviderSpecOut]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        candidates = self.candidates

        confident = self.confident

        providers = []
        for providers_item_data in self.providers:
            providers_item = providers_item_data.to_dict()
            providers.append(providers_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "candidates": candidates,
                "confident": confident,
                "providers": providers,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.provider_spec_out import ProviderSpecOut  # noqa: PLC0415

        d = dict(src_dict)
        candidates = cast(list[str], d.pop("candidates"))

        confident = d.pop("confident")

        providers = []
        _providers = d.pop("providers")
        for providers_item_data in _providers:
            providers_item = ProviderSpecOut.from_dict(providers_item_data)

            providers.append(providers_item)

        provider_detect_result = cls(
            candidates=candidates,
            confident=confident,
            providers=providers,
        )

        provider_detect_result.additional_properties = d
        return provider_detect_result

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
