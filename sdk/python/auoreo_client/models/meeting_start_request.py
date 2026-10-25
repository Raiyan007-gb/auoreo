from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="MeetingStartRequest")


@_attrs_define
class MeetingStartRequest:
    """
    Attributes:
        language (None | str | Unset): STT language hint (ISO-639-1)
        num_speakers (int | None | Unset): Known speaker count
        rtc_id (None | str | Unset): Live mode: append this RTC peer's utterances
        session_id (None | str | Unset): Session for the meeting events
        speak (bool | Unset): P2: the agent may speak (only with an attached RTC peer) Default: False.
        title (str | Unset):  Default: 'Meeting'.
    """

    language: None | str | Unset = UNSET
    num_speakers: int | None | Unset = UNSET
    rtc_id: None | str | Unset = UNSET
    session_id: None | str | Unset = UNSET
    speak: bool | Unset = False
    title: str | Unset = "Meeting"

    def to_dict(self) -> dict[str, Any]:
        language: None | str | Unset
        if isinstance(self.language, Unset):
            language = UNSET
        else:
            language = self.language

        num_speakers: int | None | Unset
        if isinstance(self.num_speakers, Unset):
            num_speakers = UNSET
        else:
            num_speakers = self.num_speakers

        rtc_id: None | str | Unset
        if isinstance(self.rtc_id, Unset):
            rtc_id = UNSET
        else:
            rtc_id = self.rtc_id

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        speak = self.speak

        title = self.title

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if language is not UNSET:
            field_dict["language"] = language
        if num_speakers is not UNSET:
            field_dict["num_speakers"] = num_speakers
        if rtc_id is not UNSET:
            field_dict["rtc_id"] = rtc_id
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if speak is not UNSET:
            field_dict["speak"] = speak
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_language(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        language = _parse_language(d.pop("language", UNSET))

        def _parse_num_speakers(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        num_speakers = _parse_num_speakers(d.pop("num_speakers", UNSET))

        def _parse_rtc_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        rtc_id = _parse_rtc_id(d.pop("rtc_id", UNSET))

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        speak = d.pop("speak", UNSET)

        title = d.pop("title", UNSET)

        meeting_start_request = cls(
            language=language,
            num_speakers=num_speakers,
            rtc_id=rtc_id,
            session_id=session_id,
            speak=speak,
            title=title,
        )

        return meeting_start_request
