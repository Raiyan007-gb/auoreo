from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.voice_segment import VoiceSegment


T = TypeVar("T", bound="VoiceTranscription")


@_attrs_define
class VoiceTranscription:
    """
    Attributes:
        backend (str): Selected stt backend
        duration_s (float): Duration of the normalized audio
        segments (list[VoiceSegment]):
        text (str):
        language (None | str | Unset):
        run_id (None | str | Unset): Followup run when `session_id` was given
    """

    backend: str
    duration_s: float
    segments: list[VoiceSegment]
    text: str
    language: None | str | Unset = UNSET
    run_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        backend = self.backend

        duration_s = self.duration_s

        segments = []
        for segments_item_data in self.segments:
            segments_item = segments_item_data.to_dict()
            segments.append(segments_item)

        text = self.text

        language: None | str | Unset
        if isinstance(self.language, Unset):
            language = UNSET
        else:
            language = self.language

        run_id: None | str | Unset
        if isinstance(self.run_id, Unset):
            run_id = UNSET
        else:
            run_id = self.run_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "backend": backend,
                "duration_s": duration_s,
                "segments": segments,
                "text": text,
            }
        )
        if language is not UNSET:
            field_dict["language"] = language
        if run_id is not UNSET:
            field_dict["run_id"] = run_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.voice_segment import VoiceSegment  # noqa: PLC0415

        d = dict(src_dict)
        backend = d.pop("backend")

        duration_s = d.pop("duration_s")

        segments = []
        _segments = d.pop("segments")
        for segments_item_data in _segments:
            segments_item = VoiceSegment.from_dict(segments_item_data)

            segments.append(segments_item)

        text = d.pop("text")

        def _parse_language(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        language = _parse_language(d.pop("language", UNSET))

        def _parse_run_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        run_id = _parse_run_id(d.pop("run_id", UNSET))

        voice_transcription = cls(
            backend=backend,
            duration_s=duration_s,
            segments=segments,
            text=text,
            language=language,
            run_id=run_id,
        )

        voice_transcription.additional_properties = d
        return voice_transcription

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
