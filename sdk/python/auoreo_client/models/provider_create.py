from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ProviderCreate")


@_attrs_define
class ProviderCreate:
    """
    Attributes:
        provider (str):
        api_version (None | str | Unset):
        base_url (None | str | Unset):
        deployments (list[str] | Unset): Azure only
        key (None | str | Unset): Write-only; never returned
        label (None | str | Unset):
    """

    provider: str
    api_version: None | str | Unset = UNSET
    base_url: None | str | Unset = UNSET
    deployments: list[str] | Unset = UNSET
    key: None | str | Unset = UNSET
    label: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        provider = self.provider

        api_version: None | str | Unset
        if isinstance(self.api_version, Unset):
            api_version = UNSET
        else:
            api_version = self.api_version

        base_url: None | str | Unset
        if isinstance(self.base_url, Unset):
            base_url = UNSET
        else:
            base_url = self.base_url

        deployments: list[str] | Unset = UNSET
        if not isinstance(self.deployments, Unset):
            deployments = self.deployments

        key: None | str | Unset
        if isinstance(self.key, Unset):
            key = UNSET
        else:
            key = self.key

        label: None | str | Unset
        if isinstance(self.label, Unset):
            label = UNSET
        else:
            label = self.label

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "provider": provider,
            }
        )
        if api_version is not UNSET:
            field_dict["api_version"] = api_version
        if base_url is not UNSET:
            field_dict["base_url"] = base_url
        if deployments is not UNSET:
            field_dict["deployments"] = deployments
        if key is not UNSET:
            field_dict["key"] = key
        if label is not UNSET:
            field_dict["label"] = label

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        provider = d.pop("provider")

        def _parse_api_version(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        api_version = _parse_api_version(d.pop("api_version", UNSET))

        def _parse_base_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        base_url = _parse_base_url(d.pop("base_url", UNSET))

        deployments = cast(list[str], d.pop("deployments", UNSET))

        def _parse_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        key = _parse_key(d.pop("key", UNSET))

        def _parse_label(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        label = _parse_label(d.pop("label", UNSET))

        provider_create = cls(
            provider=provider,
            api_version=api_version,
            base_url=base_url,
            deployments=deployments,
            key=key,
            label=label,
        )

        provider_create.additional_properties = d
        return provider_create

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
