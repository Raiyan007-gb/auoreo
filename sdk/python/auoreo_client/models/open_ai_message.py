from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.open_ai_tool_call import OpenAIToolCall


T = TypeVar("T", bound="OpenAIMessage")


@_attrs_define
class OpenAIMessage:
    """
    Attributes:
        content (None | str):
        role (Literal['assistant'] | Unset):  Default: 'assistant'.
        tool_calls (list[OpenAIToolCall] | None | Unset):
    """

    content: None | str
    role: Literal["assistant"] | Unset = "assistant"
    tool_calls: list[OpenAIToolCall] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        content: None | str
        content = self.content

        role = self.role

        tool_calls: list[dict[str, Any]] | None | Unset
        if isinstance(self.tool_calls, Unset):
            tool_calls = UNSET
        elif isinstance(self.tool_calls, list):
            tool_calls = []
            for tool_calls_type_0_item_data in self.tool_calls:
                tool_calls_type_0_item = tool_calls_type_0_item_data.to_dict()
                tool_calls.append(tool_calls_type_0_item)

        else:
            tool_calls = self.tool_calls

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "content": content,
            }
        )
        if role is not UNSET:
            field_dict["role"] = role
        if tool_calls is not UNSET:
            field_dict["tool_calls"] = tool_calls

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.open_ai_tool_call import OpenAIToolCall  # noqa: PLC0415

        d = dict(src_dict)

        def _parse_content(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        content = _parse_content(d.pop("content"))

        role = cast(Literal["assistant"] | Unset, d.pop("role", UNSET))
        if role != "assistant" and not isinstance(role, Unset):
            raise ValueError(f"role must match const 'assistant', got '{role}'")

        def _parse_tool_calls(data: object) -> list[OpenAIToolCall] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                tool_calls_type_0 = []
                _tool_calls_type_0 = data
                for tool_calls_type_0_item_data in _tool_calls_type_0:
                    tool_calls_type_0_item = OpenAIToolCall.from_dict(tool_calls_type_0_item_data)

                    tool_calls_type_0.append(tool_calls_type_0_item)

                return tool_calls_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[OpenAIToolCall] | None | Unset, data)

        tool_calls = _parse_tool_calls(d.pop("tool_calls", UNSET))

        open_ai_message = cls(
            content=content,
            role=role,
            tool_calls=tool_calls,
        )

        open_ai_message.additional_properties = d
        return open_ai_message

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
