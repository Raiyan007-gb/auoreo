from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ProviderSpecOut")


@_attrs_define
class ProviderSpecOut:
    """
    Attributes:
        id (str):
        manual_models (bool): Azure: deployment names are entered by hand
        name (str):
        needs_api_version (bool):
        needs_base_url (bool):
        needs_key (bool):
        prefix (str): LiteLLM model prefix, e.g. `openai/`
        default_api_version (None | str | Unset):
        default_base_url (None | str | Unset):
        docs_url (str | Unset):  Default: ''.
    """

    id: str
    manual_models: bool
    name: str
    needs_api_version: bool
    needs_base_url: bool
    needs_key: bool
    prefix: str
    default_api_version: None | str | Unset = UNSET
    default_base_url: None | str | Unset = UNSET
    docs_url: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        manual_models = self.manual_models

        name = self.name

        needs_api_version = self.needs_api_version

        needs_base_url = self.needs_base_url

        needs_key = self.needs_key

        prefix = self.prefix

        default_api_version: None | str | Unset
        if isinstance(self.default_api_version, Unset):
            default_api_version = UNSET
        else:
            default_api_version = self.default_api_version

        default_base_url: None | str | Unset
        if isinstance(self.default_base_url, Unset):
            default_base_url = UNSET
        else:
            default_base_url = self.default_base_url

        docs_url = self.docs_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "manual_models": manual_models,
                "name": name,
                "needs_api_version": needs_api_version,
                "needs_base_url": needs_base_url,
                "needs_key": needs_key,
                "prefix": prefix,
            }
        )
        if default_api_version is not UNSET:
            field_dict["default_api_version"] = default_api_version
        if default_base_url is not UNSET:
            field_dict["default_base_url"] = default_base_url
        if docs_url is not UNSET:
            field_dict["docs_url"] = docs_url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        manual_models = d.pop("manual_models")

        name = d.pop("name")

        needs_api_version = d.pop("needs_api_version")

        needs_base_url = d.pop("needs_base_url")

        needs_key = d.pop("needs_key")

        prefix = d.pop("prefix")

        def _parse_default_api_version(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        default_api_version = _parse_default_api_version(d.pop("default_api_version", UNSET))

        def _parse_default_base_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        default_base_url = _parse_default_base_url(d.pop("default_base_url", UNSET))

        docs_url = d.pop("docs_url", UNSET)

        provider_spec_out = cls(
            id=id,
            manual_models=manual_models,
            name=name,
            needs_api_version=needs_api_version,
            needs_base_url=needs_base_url,
            needs_key=needs_key,
            prefix=prefix,
            default_api_version=default_api_version,
            default_base_url=default_base_url,
            docs_url=docs_url,
        )

        provider_spec_out.additional_properties = d
        return provider_spec_out

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
