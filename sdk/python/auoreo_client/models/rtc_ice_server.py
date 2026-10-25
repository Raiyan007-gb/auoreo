from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="RtcIceServer")


@_attrs_define
class RtcIceServer:
    """One `RTCIceServer` entry, ready for `new RTCPeerConnection({iceServers})`.

    Attributes:
        urls (list[str]):
        credential (None | str | Unset): base64(HMAC-SHA1(secret, username)); expires with the username
        username (None | str | Unset): TURN REST username `<expiry>:<user>`
    """

    urls: list[str]
    credential: None | str | Unset = UNSET
    username: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        urls = self.urls

        credential: None | str | Unset
        if isinstance(self.credential, Unset):
            credential = UNSET
        else:
            credential = self.credential

        username: None | str | Unset
        if isinstance(self.username, Unset):
            username = UNSET
        else:
            username = self.username

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "urls": urls,
            }
        )
        if credential is not UNSET:
            field_dict["credential"] = credential
        if username is not UNSET:
            field_dict["username"] = username

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        urls = cast(list[str], d.pop("urls"))

        def _parse_credential(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        credential = _parse_credential(d.pop("credential", UNSET))

        def _parse_username(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        username = _parse_username(d.pop("username", UNSET))

        rtc_ice_server = cls(
            urls=urls,
            credential=credential,
            username=username,
        )

        rtc_ice_server.additional_properties = d
        return rtc_ice_server

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
