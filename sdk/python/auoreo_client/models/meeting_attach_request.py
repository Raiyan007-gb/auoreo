from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="MeetingAttachRequest")


@_attrs_define
class MeetingAttachRequest:
    """
    Attributes:
        rtc_id (str):
    """

    rtc_id: str

    def to_dict(self) -> dict[str, Any]:
        rtc_id = self.rtc_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "rtc_id": rtc_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        rtc_id = d.pop("rtc_id")

        meeting_attach_request = cls(
            rtc_id=rtc_id,
        )

        return meeting_attach_request
