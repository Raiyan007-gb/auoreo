from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="MeetingChunkInfo")


@_attrs_define
class MeetingChunkInfo:
    """
    Attributes:
        duration_s (float):
        index (int):
        meeting_id (str):
        total_s (float):
    """

    duration_s: float
    index: int
    meeting_id: str
    total_s: float
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        duration_s = self.duration_s

        index = self.index

        meeting_id = self.meeting_id

        total_s = self.total_s

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "duration_s": duration_s,
                "index": index,
                "meeting_id": meeting_id,
                "total_s": total_s,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        duration_s = d.pop("duration_s")

        index = d.pop("index")

        meeting_id = d.pop("meeting_id")

        total_s = d.pop("total_s")

        meeting_chunk_info = cls(
            duration_s=duration_s,
            index=index,
            meeting_id=meeting_id,
            total_s=total_s,
        )

        meeting_chunk_info.additional_properties = d
        return meeting_chunk_info

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
