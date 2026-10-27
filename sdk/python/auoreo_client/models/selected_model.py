from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.selected_model_role import SelectedModelRole
from ..types import UNSET, Unset

T = TypeVar("T", bound="SelectedModel")


@_attrs_define
class SelectedModel:
    """
    Attributes:
        role (SelectedModelRole):
        source (str): env | db | override | default
        value (None | str): The effective setting, e.g. `openai/gpt-5`
        connection_id (int | None | Unset):
        model_id (None | str | Unset):
        provider (None | str | Unset):
    """

    role: SelectedModelRole
    source: str
    value: None | str
    connection_id: int | None | Unset = UNSET
    model_id: None | str | Unset = UNSET
    provider: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        role = self.role.value

        source = self.source

        value: None | str
        value = self.value

        connection_id: int | None | Unset
        if isinstance(self.connection_id, Unset):
            connection_id = UNSET
        else:
            connection_id = self.connection_id

        model_id: None | str | Unset
        if isinstance(self.model_id, Unset):
            model_id = UNSET
        else:
            model_id = self.model_id

        provider: None | str | Unset
        if isinstance(self.provider, Unset):
            provider = UNSET
        else:
            provider = self.provider

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "role": role,
                "source": source,
                "value": value,
            }
        )
        if connection_id is not UNSET:
            field_dict["connection_id"] = connection_id
        if model_id is not UNSET:
            field_dict["model_id"] = model_id
        if provider is not UNSET:
            field_dict["provider"] = provider

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        role = SelectedModelRole(d.pop("role"))

        source = d.pop("source")

        def _parse_value(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        value = _parse_value(d.pop("value"))

        def _parse_connection_id(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        connection_id = _parse_connection_id(d.pop("connection_id", UNSET))

        def _parse_model_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model_id = _parse_model_id(d.pop("model_id", UNSET))

        def _parse_provider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider = _parse_provider(d.pop("provider", UNSET))

        selected_model = cls(
            role=role,
            source=source,
            value=value,
            connection_id=connection_id,
            model_id=model_id,
            provider=provider,
        )

        selected_model.additional_properties = d
        return selected_model

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
