from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="RtcAnswer")


@_attrs_define
class RtcAnswer:
    """
    Attributes:
        rtc_id (str):
        sdp (str):
        type_ (Literal['answer'] | Unset):  Default: 'answer'.
    """

    rtc_id: str
    sdp: str
    type_: Literal["answer"] | Unset = "answer"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        rtc_id = self.rtc_id

        sdp = self.sdp

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "rtc_id": rtc_id,
                "sdp": sdp,
            }
        )
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        rtc_id = d.pop("rtc_id")

        sdp = d.pop("sdp")

        type_ = cast(Literal["answer"] | Unset, d.pop("type", UNSET))
        if type_ != "answer" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'answer', got '{type_}'")

        rtc_answer = cls(
            rtc_id=rtc_id,
            sdp=sdp,
            type_=type_,
        )

        rtc_answer.additional_properties = d
        return rtc_answer

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
