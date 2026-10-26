from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="CapabilityToken")


@_attrs_define
class CapabilityToken:
    """A `/ws` credential scoped to one session (`serve --ws-auth capability`); pass it as
    `?token=` or `Authorization: Bearer` on the handshake. Never logged.

        Attributes:
            expires_at (int):
            session_id (str):
            token (str):
    """

    expires_at: int
    session_id: str
    token: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        expires_at = self.expires_at

        session_id = self.session_id

        token = self.token

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "expires_at": expires_at,
                "session_id": session_id,
                "token": token,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expires_at = d.pop("expires_at")

        session_id = d.pop("session_id")

        token = d.pop("token")

        capability_token = cls(
            expires_at=expires_at,
            session_id=session_id,
            token=token,
        )

        capability_token.additional_properties = d
        return capability_token

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
