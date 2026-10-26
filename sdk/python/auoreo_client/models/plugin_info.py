from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.plugin_info_status import PluginInfoStatus
from ..models.plugin_info_verify import PluginInfoVerify
from ..types import UNSET, Unset

T = TypeVar("T", bound="PluginInfo")


@_attrs_define
class PluginInfo:
    """
    Attributes:
        description (str):
        name (str):
        provides (list[str]):
        status (PluginInfoStatus):
        tools (list[str]): Registered dynamic tools (`plugin__<name>__<tool>`)
        verify (PluginInfoVerify): lock.json hash vs the plugin folder
        version (str):
        connectors (list[str] | Unset):
        deliveries (list[str] | Unset):
        error (None | str | Unset):
        signed_by (None | str | Unset): ed25519 key id that signed it (I6)
        tier (None | str | Unset): bundled | allow-listed | local-path (I6)
    """

    description: str
    name: str
    provides: list[str]
    status: PluginInfoStatus
    tools: list[str]
    verify: PluginInfoVerify
    version: str
    connectors: list[str] | Unset = UNSET
    deliveries: list[str] | Unset = UNSET
    error: None | str | Unset = UNSET
    signed_by: None | str | Unset = UNSET
    tier: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        name = self.name

        provides = self.provides

        status = self.status.value

        tools = self.tools

        verify = self.verify.value

        version = self.version

        connectors: list[str] | Unset = UNSET
        if not isinstance(self.connectors, Unset):
            connectors = self.connectors

        deliveries: list[str] | Unset = UNSET
        if not isinstance(self.deliveries, Unset):
            deliveries = self.deliveries

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        signed_by: None | str | Unset
        if isinstance(self.signed_by, Unset):
            signed_by = UNSET
        else:
            signed_by = self.signed_by

        tier: None | str | Unset
        if isinstance(self.tier, Unset):
            tier = UNSET
        else:
            tier = self.tier

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "name": name,
                "provides": provides,
                "status": status,
                "tools": tools,
                "verify": verify,
                "version": version,
            }
        )
        if connectors is not UNSET:
            field_dict["connectors"] = connectors
        if deliveries is not UNSET:
            field_dict["deliveries"] = deliveries
        if error is not UNSET:
            field_dict["error"] = error
        if signed_by is not UNSET:
            field_dict["signed_by"] = signed_by
        if tier is not UNSET:
            field_dict["tier"] = tier

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        description = d.pop("description")

        name = d.pop("name")

        provides = cast(list[str], d.pop("provides"))

        status = PluginInfoStatus(d.pop("status"))

        tools = cast(list[str], d.pop("tools"))

        verify = PluginInfoVerify(d.pop("verify"))

        version = d.pop("version")

        connectors = cast(list[str], d.pop("connectors", UNSET))

        deliveries = cast(list[str], d.pop("deliveries", UNSET))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_signed_by(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        signed_by = _parse_signed_by(d.pop("signed_by", UNSET))

        def _parse_tier(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tier = _parse_tier(d.pop("tier", UNSET))

        plugin_info = cls(
            description=description,
            name=name,
            provides=provides,
            status=status,
            tools=tools,
            verify=verify,
            version=version,
            connectors=connectors,
            deliveries=deliveries,
            error=error,
            signed_by=signed_by,
            tier=tier,
        )

        plugin_info.additional_properties = d
        return plugin_info

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
