from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.rtc_ice_server import RtcIceServer


T = TypeVar("T", bound="RtcIceConfig")


@_attrs_define
class RtcIceConfig:
    """
    Attributes:
        ice_servers (list[RtcIceServer]): Pass as `RTCConfiguration.iceServers`
        expires_at (int | None | Unset): Unix time the TURN credentials stop working (None = no TURN REST)
        ttl_s (int | None | Unset):
    """

    ice_servers: list[RtcIceServer]
    expires_at: int | None | Unset = UNSET
    ttl_s: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        ice_servers = []
        for ice_servers_item_data in self.ice_servers:
            ice_servers_item = ice_servers_item_data.to_dict()
            ice_servers.append(ice_servers_item)

        expires_at: int | None | Unset
        if isinstance(self.expires_at, Unset):
            expires_at = UNSET
        else:
            expires_at = self.expires_at

        ttl_s: int | None | Unset
        if isinstance(self.ttl_s, Unset):
            ttl_s = UNSET
        else:
            ttl_s = self.ttl_s

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "iceServers": ice_servers,
            }
        )
        if expires_at is not UNSET:
            field_dict["expires_at"] = expires_at
        if ttl_s is not UNSET:
            field_dict["ttl_s"] = ttl_s

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.rtc_ice_server import RtcIceServer  # noqa: PLC0415

        d = dict(src_dict)
        ice_servers = []
        _ice_servers = d.pop("iceServers")
        for ice_servers_item_data in _ice_servers:
            ice_servers_item = RtcIceServer.from_dict(ice_servers_item_data)

            ice_servers.append(ice_servers_item)

        def _parse_expires_at(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        expires_at = _parse_expires_at(d.pop("expires_at", UNSET))

        def _parse_ttl_s(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        ttl_s = _parse_ttl_s(d.pop("ttl_s", UNSET))

        rtc_ice_config = cls(
            ice_servers=ice_servers,
            expires_at=expires_at,
            ttl_s=ttl_s,
        )

        rtc_ice_config.additional_properties = d
        return rtc_ice_config

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
