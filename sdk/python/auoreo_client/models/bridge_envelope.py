from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="BridgeEnvelope")


@_attrs_define
class BridgeEnvelope:
    """`POST /api/bridge/inbox` body. Signed with HMAC-SHA256 over
    `"<timestamp>.<nonce>." + raw body` (header `X-Auoreo-Signature: sha256=<hex>`).

        Attributes:
            from_member (str):
            message (str):
            nonce (str):
            sender (str): Sending instance, as the receiver's `bridges` key
            timestamp (int): Unix seconds; accepted within ±300 s
            to_member (str):
            summary (str | Unset):  Default: ''.
            team (None | str | Unset): Receiver team id or name
    """

    from_member: str
    message: str
    nonce: str
    sender: str
    timestamp: int
    to_member: str
    summary: str | Unset = ""
    team: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from_member = self.from_member

        message = self.message

        nonce = self.nonce

        sender = self.sender

        timestamp = self.timestamp

        to_member = self.to_member

        summary = self.summary

        team: None | str | Unset
        if isinstance(self.team, Unset):
            team = UNSET
        else:
            team = self.team

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "from_member": from_member,
                "message": message,
                "nonce": nonce,
                "sender": sender,
                "timestamp": timestamp,
                "to_member": to_member,
            }
        )
        if summary is not UNSET:
            field_dict["summary"] = summary
        if team is not UNSET:
            field_dict["team"] = team

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        from_member = d.pop("from_member")

        message = d.pop("message")

        nonce = d.pop("nonce")

        sender = d.pop("sender")

        timestamp = d.pop("timestamp")

        to_member = d.pop("to_member")

        summary = d.pop("summary", UNSET)

        def _parse_team(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        team = _parse_team(d.pop("team", UNSET))

        bridge_envelope = cls(
            from_member=from_member,
            message=message,
            nonce=nonce,
            sender=sender,
            timestamp=timestamp,
            to_member=to_member,
            summary=summary,
            team=team,
        )

        return bridge_envelope
