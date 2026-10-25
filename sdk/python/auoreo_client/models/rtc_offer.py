from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RtcOffer")


@_attrs_define
class RtcOffer:
    """Client SDP offer. The client must create the `auoreo-events` data channel first.

    Attributes:
        sdp (str): Offer SDP (≤ 64 KiB, else 413); never logged
        session_id (None | str | Unset): Auto-subscribe the events channel to it
        type_ (Literal['offer'] | Unset):  Default: 'offer'.
    """

    sdp: str
    session_id: None | str | Unset = UNSET
    type_: Literal["offer"] | Unset = "offer"

    def to_dict(self) -> dict[str, Any]:
        sdp = self.sdp

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "sdp": sdp,
            }
        )
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        sdp = d.pop("sdp")

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        type_ = cast(Literal["offer"] | Unset, d.pop("type", UNSET))
        if type_ != "offer" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'offer', got '{type_}'")

        rtc_offer = cls(
            sdp=sdp,
            session_id=session_id,
            type_=type_,
        )

        return rtc_offer
