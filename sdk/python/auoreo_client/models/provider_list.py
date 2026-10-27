from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.provider_connection_out import ProviderConnectionOut
    from ..models.provider_spec_out import ProviderSpecOut


T = TypeVar("T", bound="ProviderList")


@_attrs_define
class ProviderList:
    """
    Attributes:
        connections (list[ProviderConnectionOut]):
        providers (list[ProviderSpecOut]): Every provider that can be connected
    """

    connections: list[ProviderConnectionOut]
    providers: list[ProviderSpecOut]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connections = []
        for connections_item_data in self.connections:
            connections_item = connections_item_data.to_dict()
            connections.append(connections_item)

        providers = []
        for providers_item_data in self.providers:
            providers_item = providers_item_data.to_dict()
            providers.append(providers_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connections": connections,
                "providers": providers,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.provider_connection_out import ProviderConnectionOut  # noqa: PLC0415
        from ..models.provider_spec_out import ProviderSpecOut  # noqa: PLC0415

        d = dict(src_dict)
        connections = []
        _connections = d.pop("connections")
        for connections_item_data in _connections:
            connections_item = ProviderConnectionOut.from_dict(connections_item_data)

            connections.append(connections_item)

        providers = []
        _providers = d.pop("providers")
        for providers_item_data in _providers:
            providers_item = ProviderSpecOut.from_dict(providers_item_data)

            providers.append(providers_item)

        provider_list = cls(
            connections=connections,
            providers=providers,
        )

        provider_list.additional_properties = d
        return provider_list

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
