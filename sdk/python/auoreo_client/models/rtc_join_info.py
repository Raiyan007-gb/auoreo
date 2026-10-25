from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.rtc_join_info_extra import RtcJoinInfoExtra


T = TypeVar("T", bound="RtcJoinInfo")


@_attrs_define
class RtcJoinInfo:
    """
    Attributes:
        join_id (str): Pass to `DELETE /api/rtc/join/{join_id}` to leave
        transport (str): Backend name, e.g. `aiortc` or `livekit`
        extra (RtcJoinInfoExtra | Unset):
        room (None | str | Unset):
        sdp (None | str | Unset): SDP answer (P2P transports)
        session_id (None | str | Unset):
        token (None | str | Unset): Room-scoped access token (LiveKit)
        url (None | str | Unset): SFU URL to connect to (LiveKit)
    """

    join_id: str
    transport: str
    extra: RtcJoinInfoExtra | Unset = UNSET
    room: None | str | Unset = UNSET
    sdp: None | str | Unset = UNSET
    session_id: None | str | Unset = UNSET
    token: None | str | Unset = UNSET
    url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        join_id = self.join_id

        transport = self.transport

        extra: dict[str, Any] | Unset = UNSET
        if not isinstance(self.extra, Unset):
            extra = self.extra.to_dict()

        room: None | str | Unset
        if isinstance(self.room, Unset):
            room = UNSET
        else:
            room = self.room

        sdp: None | str | Unset
        if isinstance(self.sdp, Unset):
            sdp = UNSET
        else:
            sdp = self.sdp

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        token: None | str | Unset
        if isinstance(self.token, Unset):
            token = UNSET
        else:
            token = self.token

        url: None | str | Unset
        if isinstance(self.url, Unset):
            url = UNSET
        else:
            url = self.url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "join_id": join_id,
                "transport": transport,
            }
        )
        if extra is not UNSET:
            field_dict["extra"] = extra
        if room is not UNSET:
            field_dict["room"] = room
        if sdp is not UNSET:
            field_dict["sdp"] = sdp
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if token is not UNSET:
            field_dict["token"] = token
        if url is not UNSET:
            field_dict["url"] = url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.rtc_join_info_extra import RtcJoinInfoExtra  # noqa: PLC0415

        d = dict(src_dict)
        join_id = d.pop("join_id")

        transport = d.pop("transport")

        _extra = d.pop("extra", UNSET)
        extra: RtcJoinInfoExtra | Unset
        if isinstance(_extra, Unset):
            extra = UNSET
        else:
            extra = RtcJoinInfoExtra.from_dict(_extra)

        def _parse_room(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        room = _parse_room(d.pop("room", UNSET))

        def _parse_sdp(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        sdp = _parse_sdp(d.pop("sdp", UNSET))

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        def _parse_token(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        token = _parse_token(d.pop("token", UNSET))

        def _parse_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        url = _parse_url(d.pop("url", UNSET))

        rtc_join_info = cls(
            join_id=join_id,
            transport=transport,
            extra=extra,
            room=room,
            sdp=sdp,
            session_id=session_id,
            token=token,
            url=url,
        )

        rtc_join_info.additional_properties = d
        return rtc_join_info

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
