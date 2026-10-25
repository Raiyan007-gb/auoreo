from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="MailMessage")


@_attrs_define
class MailMessage:
    """
    Attributes:
        created_at (datetime.datetime):
        id (int):
        read (bool):
        recipient (str):
        sender (str):
        summary (str):
        team_id (str):
        text (str):
        type_ (str):
    """

    created_at: datetime.datetime
    id: int
    read: bool
    recipient: str
    sender: str
    summary: str
    team_id: str
    text: str
    type_: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = self.id

        read = self.read

        recipient = self.recipient

        sender = self.sender

        summary = self.summary

        team_id = self.team_id

        text = self.text

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "read": read,
                "recipient": recipient,
                "sender": sender,
                "summary": summary,
                "team_id": team_id,
                "text": text,
                "type": type_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        read = d.pop("read")

        recipient = d.pop("recipient")

        sender = d.pop("sender")

        summary = d.pop("summary")

        team_id = d.pop("team_id")

        text = d.pop("text")

        type_ = d.pop("type")

        mail_message = cls(
            created_at=created_at,
            id=id,
            read=read,
            recipient=recipient,
            sender=sender,
            summary=summary,
            team_id=team_id,
            text=text,
            type_=type_,
        )

        mail_message.additional_properties = d
        return mail_message

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
