from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="BridgeDelivered")


@_attrs_define
class BridgeDelivered:
    """
    Attributes:
        mail_id (int):
        recipient (str):
        sender (str): As stored in the receiver's mailbox: `bridge:<instance>/<member>`
        team (str):
    """

    mail_id: int
    recipient: str
    sender: str
    team: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        mail_id = self.mail_id

        recipient = self.recipient

        sender = self.sender

        team = self.team

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "mail_id": mail_id,
                "recipient": recipient,
                "sender": sender,
                "team": team,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        mail_id = d.pop("mail_id")

        recipient = d.pop("recipient")

        sender = d.pop("sender")

        team = d.pop("team")

        bridge_delivered = cls(
            mail_id=mail_id,
            recipient=recipient,
            sender=sender,
            team=team,
        )

        bridge_delivered.additional_properties = d
        return bridge_delivered

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
