from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="RtcOffer")


@_attrs_define
class RtcOffer:
    """Client SDP offer. The client must create the `auoreo-events` data channel first.

    F1: offer an audio m-line (`sendrecv`, or `recvonly` to only listen) to hear the agent;
    `input: true` additionally accepts an `auoreo-input` data channel (teleop).

        Attributes:
            sdp (str): Offer SDP (≤ 64 KiB, else 413); never logged
            input_ (bool | Unset): Accept the `auoreo-input` teleop channel; 403 unless `settings.teleop_input_enabled`
                Default: False.
            session_id (None | str | Unset): Auto-subscribe the events channel to it
            speak (bool | None | Unset): `false` opts this peer out of spoken replies (default:
                `settings.voice_speak_over_rtc`)
            type_ (Literal['offer'] | Unset):  Default: 'offer'.
    """

    sdp: str
    input_: bool | Unset = False
    session_id: None | str | Unset = UNSET
    speak: bool | None | Unset = UNSET
    type_: Literal["offer"] | Unset = "offer"

    def to_dict(self) -> dict[str, Any]:
        sdp = self.sdp

        input_ = self.input_

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        speak: bool | None | Unset
        if isinstance(self.speak, Unset):
            speak = UNSET
        else:
            speak = self.speak

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "sdp": sdp,
            }
        )
        if input_ is not UNSET:
            field_dict["input"] = input_
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if speak is not UNSET:
            field_dict["speak"] = speak
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        sdp = d.pop("sdp")

        input_ = d.pop("input", UNSET)

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        def _parse_speak(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        speak = _parse_speak(d.pop("speak", UNSET))

        type_ = cast(Literal["offer"] | Unset, d.pop("type", UNSET))
        if type_ != "offer" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'offer', got '{type_}'")

        rtc_offer = cls(
            sdp=sdp,
            input_=input_,
            session_id=session_id,
            speak=speak,
            type_=type_,
        )

        return rtc_offer
