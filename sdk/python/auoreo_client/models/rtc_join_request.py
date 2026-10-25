from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RtcJoinRequest")


@_attrs_define
class RtcJoinRequest:
    """
    Attributes:
        identity (str | Unset): Participant identity (LiveKit token subject) Default: 'user'.
        sdp (None | str | Unset): SDP offer; required by the aiortc transport
        session_id (None | str | Unset): Session the media belongs to
    """

    identity: str | Unset = "user"
    sdp: None | str | Unset = UNSET
    session_id: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        identity = self.identity

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

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if identity is not UNSET:
            field_dict["identity"] = identity
        if sdp is not UNSET:
            field_dict["sdp"] = sdp
        if session_id is not UNSET:
            field_dict["session_id"] = session_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        identity = d.pop("identity", UNSET)

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

        rtc_join_request = cls(
            identity=identity,
            sdp=sdp,
            session_id=session_id,
        )

        return rtc_join_request
