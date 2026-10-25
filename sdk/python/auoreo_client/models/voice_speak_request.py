from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.voice_speak_request_format_type_0 import VoiceSpeakRequestFormatType0
from ..types import UNSET, Unset

T = TypeVar("T", bound="VoiceSpeakRequest")


@_attrs_define
class VoiceSpeakRequest:
    """
    Attributes:
        text (str):
        format_ (None | Unset | VoiceSpeakRequestFormatType0): Transcode the backend output (ffmpeg); default = backend
            native
        session_id (None | str | Unset):
        speed (float | Unset):  Default: 1.0.
        voice (None | str | Unset):
    """

    text: str
    format_: None | Unset | VoiceSpeakRequestFormatType0 = UNSET
    session_id: None | str | Unset = UNSET
    speed: float | Unset = 1.0
    voice: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        text = self.text

        format_: None | str | Unset
        if isinstance(self.format_, Unset):
            format_ = UNSET
        elif isinstance(self.format_, VoiceSpeakRequestFormatType0):
            format_ = self.format_.value
        else:
            format_ = self.format_

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        speed = self.speed

        voice: None | str | Unset
        if isinstance(self.voice, Unset):
            voice = UNSET
        else:
            voice = self.voice

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "text": text,
            }
        )
        if format_ is not UNSET:
            field_dict["format"] = format_
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if speed is not UNSET:
            field_dict["speed"] = speed
        if voice is not UNSET:
            field_dict["voice"] = voice

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        text = d.pop("text")

        def _parse_format_(data: object) -> None | Unset | VoiceSpeakRequestFormatType0:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                format_type_0 = VoiceSpeakRequestFormatType0(data)

                return format_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | Unset | VoiceSpeakRequestFormatType0, data)

        format_ = _parse_format_(d.pop("format", UNSET))

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        speed = d.pop("speed", UNSET)

        def _parse_voice(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        voice = _parse_voice(d.pop("voice", UNSET))

        voice_speak_request = cls(
            text=text,
            format_=format_,
            session_id=session_id,
            speed=speed,
            voice=voice,
        )

        return voice_speak_request
