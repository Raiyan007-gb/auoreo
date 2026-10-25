from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.open_ai_message import OpenAIMessage


T = TypeVar("T", bound="OpenAIChoice")


@_attrs_define
class OpenAIChoice:
    """
    Attributes:
        finish_reason (str):
        message (OpenAIMessage):
        index (int | Unset):  Default: 0.
    """

    finish_reason: str
    message: OpenAIMessage
    index: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        finish_reason = self.finish_reason

        message = self.message.to_dict()

        index = self.index

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "finish_reason": finish_reason,
                "message": message,
            }
        )
        if index is not UNSET:
            field_dict["index"] = index

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.open_ai_message import OpenAIMessage  # noqa: PLC0415

        d = dict(src_dict)
        finish_reason = d.pop("finish_reason")

        message = OpenAIMessage.from_dict(d.pop("message"))

        index = d.pop("index", UNSET)

        open_ai_choice = cls(
            finish_reason=finish_reason,
            message=message,
            index=index,
        )

        open_ai_choice.additional_properties = d
        return open_ai_choice

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
