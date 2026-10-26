from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="LiveKitToken")


@_attrs_define
class LiveKitToken:
    """
    Attributes:
        expires_in (int): Token lifetime in seconds (≤ 900)
        participant_identity (str):
        participant_token (str): Room-scoped JWT (never logged or audited)
        room_name (str):
        server_url (str):
    """

    expires_in: int
    participant_identity: str
    participant_token: str
    room_name: str
    server_url: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        expires_in = self.expires_in

        participant_identity = self.participant_identity

        participant_token = self.participant_token

        room_name = self.room_name

        server_url = self.server_url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "expires_in": expires_in,
                "participant_identity": participant_identity,
                "participant_token": participant_token,
                "room_name": room_name,
                "server_url": server_url,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expires_in = d.pop("expires_in")

        participant_identity = d.pop("participant_identity")

        participant_token = d.pop("participant_token")

        room_name = d.pop("room_name")

        server_url = d.pop("server_url")

        live_kit_token = cls(
            expires_in=expires_in,
            participant_identity=participant_identity,
            participant_token=participant_token,
            room_name=room_name,
            server_url=server_url,
        )

        live_kit_token.additional_properties = d
        return live_kit_token

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
