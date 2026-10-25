from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.meeting_detail_status import MeetingDetailStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.meeting_segment import MeetingSegment


T = TypeVar("T", bound="MeetingDetail")


@_attrs_define
class MeetingDetail:
    """
    Attributes:
        created_at (datetime.datetime):
        id (str):
        status (MeetingDetailStatus):
        title (str):
        chunks (int | Unset):  Default: 0.
        duration_s (float | Unset):  Default: 0.0.
        error (None | str | Unset):
        finalised_at (datetime.datetime | None | Unset):
        ingested (bool | Unset):  Default: False.
        language (None | str | Unset):
        live (bool | Unset): An RTC peer is attached (live utterances) Default: False.
        markdown (str | Unset):  Default: ''.
        session_id (None | str | Unset):
        speak (bool | Unset):  Default: False.
        speakers (list[str] | Unset):
        summary (None | str | Unset):
        summary_session_id (None | str | Unset):
        transcript (list[MeetingSegment] | Unset):
    """

    created_at: datetime.datetime
    id: str
    status: MeetingDetailStatus
    title: str
    chunks: int | Unset = 0
    duration_s: float | Unset = 0.0
    error: None | str | Unset = UNSET
    finalised_at: datetime.datetime | None | Unset = UNSET
    ingested: bool | Unset = False
    language: None | str | Unset = UNSET
    live: bool | Unset = False
    markdown: str | Unset = ""
    session_id: None | str | Unset = UNSET
    speak: bool | Unset = False
    speakers: list[str] | Unset = UNSET
    summary: None | str | Unset = UNSET
    summary_session_id: None | str | Unset = UNSET
    transcript: list[MeetingSegment] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = self.id

        status = self.status.value

        title = self.title

        chunks = self.chunks

        duration_s = self.duration_s

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        finalised_at: None | str | Unset
        if isinstance(self.finalised_at, Unset):
            finalised_at = UNSET
        elif isinstance(self.finalised_at, datetime.datetime):
            finalised_at = self.finalised_at.isoformat()
        else:
            finalised_at = self.finalised_at

        ingested = self.ingested

        language: None | str | Unset
        if isinstance(self.language, Unset):
            language = UNSET
        else:
            language = self.language

        live = self.live

        markdown = self.markdown

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        speak = self.speak

        speakers: list[str] | Unset = UNSET
        if not isinstance(self.speakers, Unset):
            speakers = self.speakers

        summary: None | str | Unset
        if isinstance(self.summary, Unset):
            summary = UNSET
        else:
            summary = self.summary

        summary_session_id: None | str | Unset
        if isinstance(self.summary_session_id, Unset):
            summary_session_id = UNSET
        else:
            summary_session_id = self.summary_session_id

        transcript: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.transcript, Unset):
            transcript = []
            for transcript_item_data in self.transcript:
                transcript_item = transcript_item_data.to_dict()
                transcript.append(transcript_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "status": status,
                "title": title,
            }
        )
        if chunks is not UNSET:
            field_dict["chunks"] = chunks
        if duration_s is not UNSET:
            field_dict["duration_s"] = duration_s
        if error is not UNSET:
            field_dict["error"] = error
        if finalised_at is not UNSET:
            field_dict["finalised_at"] = finalised_at
        if ingested is not UNSET:
            field_dict["ingested"] = ingested
        if language is not UNSET:
            field_dict["language"] = language
        if live is not UNSET:
            field_dict["live"] = live
        if markdown is not UNSET:
            field_dict["markdown"] = markdown
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if speak is not UNSET:
            field_dict["speak"] = speak
        if speakers is not UNSET:
            field_dict["speakers"] = speakers
        if summary is not UNSET:
            field_dict["summary"] = summary
        if summary_session_id is not UNSET:
            field_dict["summary_session_id"] = summary_session_id
        if transcript is not UNSET:
            field_dict["transcript"] = transcript

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.meeting_segment import MeetingSegment  # noqa: PLC0415

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        status = MeetingDetailStatus(d.pop("status"))

        title = d.pop("title")

        chunks = d.pop("chunks", UNSET)

        duration_s = d.pop("duration_s", UNSET)

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_finalised_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                finalised_at_type_0 = datetime.datetime.fromisoformat(data)

                return finalised_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        finalised_at = _parse_finalised_at(d.pop("finalised_at", UNSET))

        ingested = d.pop("ingested", UNSET)

        def _parse_language(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        language = _parse_language(d.pop("language", UNSET))

        live = d.pop("live", UNSET)

        markdown = d.pop("markdown", UNSET)

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        speak = d.pop("speak", UNSET)

        speakers = cast(list[str], d.pop("speakers", UNSET))

        def _parse_summary(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        summary = _parse_summary(d.pop("summary", UNSET))

        def _parse_summary_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        summary_session_id = _parse_summary_session_id(d.pop("summary_session_id", UNSET))

        _transcript = d.pop("transcript", UNSET)
        transcript: list[MeetingSegment] | Unset = UNSET
        if _transcript is not UNSET:
            transcript = []
            for transcript_item_data in _transcript:
                transcript_item = MeetingSegment.from_dict(transcript_item_data)

                transcript.append(transcript_item)

        meeting_detail = cls(
            created_at=created_at,
            id=id,
            status=status,
            title=title,
            chunks=chunks,
            duration_s=duration_s,
            error=error,
            finalised_at=finalised_at,
            ingested=ingested,
            language=language,
            live=live,
            markdown=markdown,
            session_id=session_id,
            speak=speak,
            speakers=speakers,
            summary=summary,
            summary_session_id=summary_session_id,
            transcript=transcript,
        )

        meeting_detail.additional_properties = d
        return meeting_detail

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
