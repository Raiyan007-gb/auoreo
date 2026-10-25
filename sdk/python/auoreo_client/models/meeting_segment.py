from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="MeetingSegment")


@_attrs_define
class MeetingSegment:
    """
    Attributes:
        end (float):
        speaker (str): Diarization label, e.g. SPEAKER_00
        start (float):
        text (str):
        confidence (float | None | Unset):
        tone (None | str | Unset): Tone classifier label for the utterance
    """

    end: float
    speaker: str
    start: float
    text: str
    confidence: float | None | Unset = UNSET
    tone: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        end = self.end

        speaker = self.speaker

        start = self.start

        text = self.text

        confidence: float | None | Unset
        if isinstance(self.confidence, Unset):
            confidence = UNSET
        else:
            confidence = self.confidence

        tone: None | str | Unset
        if isinstance(self.tone, Unset):
            tone = UNSET
        else:
            tone = self.tone

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "end": end,
                "speaker": speaker,
                "start": start,
                "text": text,
            }
        )
        if confidence is not UNSET:
            field_dict["confidence"] = confidence
        if tone is not UNSET:
            field_dict["tone"] = tone

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        end = d.pop("end")

        speaker = d.pop("speaker")

        start = d.pop("start")

        text = d.pop("text")

        def _parse_confidence(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        confidence = _parse_confidence(d.pop("confidence", UNSET))

        def _parse_tone(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tone = _parse_tone(d.pop("tone", UNSET))

        meeting_segment = cls(
            end=end,
            speaker=speaker,
            start=start,
            text=text,
            confidence=confidence,
            tone=tone,
        )

        meeting_segment.additional_properties = d
        return meeting_segment

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
