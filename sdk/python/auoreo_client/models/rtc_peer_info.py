from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="RtcPeerInfo")


@_attrs_define
class RtcPeerInfo:
    """
    Attributes:
        created_at (datetime.datetime):
        rtc_id (str):
        session_id (None | str):
        state (str): RTCPeerConnection.connectionState
    """

    created_at: datetime.datetime
    rtc_id: str
    session_id: None | str
    state: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        rtc_id = self.rtc_id

        session_id: None | str
        session_id = self.session_id

        state = self.state

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "rtc_id": rtc_id,
                "session_id": session_id,
                "state": state,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        rtc_id = d.pop("rtc_id")

        def _parse_session_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        session_id = _parse_session_id(d.pop("session_id"))

        state = d.pop("state")

        rtc_peer_info = cls(
            created_at=created_at,
            rtc_id=rtc_id,
            session_id=session_id,
            state=state,
        )

        rtc_peer_info.additional_properties = d
        return rtc_peer_info

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
