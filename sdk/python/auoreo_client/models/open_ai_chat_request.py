from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.open_ai_chat_request_messages_item import OpenAIChatRequestMessagesItem
    from ..models.open_ai_chat_request_stream_options_type_0 import OpenAIChatRequestStreamOptionsType0
    from ..models.open_ai_chat_request_tool_choice_type_1 import OpenAIChatRequestToolChoiceType1
    from ..models.open_ai_chat_request_tools_type_0_item import OpenAIChatRequestToolsType0Item


T = TypeVar("T", bound="OpenAIChatRequest")


@_attrs_define
class OpenAIChatRequest:
    """`POST /v1/chat/completions` body. Unknown OpenAI fields (`n`, `top_p`, …) are
    accepted and ignored; tools are the client's — nothing is executed server-side.

        Attributes:
            messages (list[OpenAIChatRequestMessagesItem]):
            model (str):
            max_completion_tokens (int | None | Unset):
            max_tokens (int | None | Unset):
            stream (bool | Unset):  Default: False.
            stream_options (None | OpenAIChatRequestStreamOptionsType0 | Unset):
            temperature (float | None | Unset):
            tool_choice (None | OpenAIChatRequestToolChoiceType1 | str | Unset):
            tools (list[OpenAIChatRequestToolsType0Item] | None | Unset):
            user (None | str | Unset):
    """

    messages: list[OpenAIChatRequestMessagesItem]
    model: str
    max_completion_tokens: int | None | Unset = UNSET
    max_tokens: int | None | Unset = UNSET
    stream: bool | Unset = False
    stream_options: None | OpenAIChatRequestStreamOptionsType0 | Unset = UNSET
    temperature: float | None | Unset = UNSET
    tool_choice: None | OpenAIChatRequestToolChoiceType1 | str | Unset = UNSET
    tools: list[OpenAIChatRequestToolsType0Item] | None | Unset = UNSET
    user: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.open_ai_chat_request_stream_options_type_0 import OpenAIChatRequestStreamOptionsType0  # noqa: PLC0415
        from ..models.open_ai_chat_request_tool_choice_type_1 import OpenAIChatRequestToolChoiceType1  # noqa: PLC0415

        messages = []
        for messages_item_data in self.messages:
            messages_item = messages_item_data.to_dict()
            messages.append(messages_item)

        model = self.model

        max_completion_tokens: int | None | Unset
        if isinstance(self.max_completion_tokens, Unset):
            max_completion_tokens = UNSET
        else:
            max_completion_tokens = self.max_completion_tokens

        max_tokens: int | None | Unset
        if isinstance(self.max_tokens, Unset):
            max_tokens = UNSET
        else:
            max_tokens = self.max_tokens

        stream = self.stream

        stream_options: dict[str, Any] | None | Unset
        if isinstance(self.stream_options, Unset):
            stream_options = UNSET
        elif isinstance(self.stream_options, OpenAIChatRequestStreamOptionsType0):
            stream_options = self.stream_options.to_dict()
        else:
            stream_options = self.stream_options

        temperature: float | None | Unset
        if isinstance(self.temperature, Unset):
            temperature = UNSET
        else:
            temperature = self.temperature

        tool_choice: dict[str, Any] | None | str | Unset
        if isinstance(self.tool_choice, Unset):
            tool_choice = UNSET
        elif isinstance(self.tool_choice, OpenAIChatRequestToolChoiceType1):
            tool_choice = self.tool_choice.to_dict()
        else:
            tool_choice = self.tool_choice

        tools: list[dict[str, Any]] | None | Unset
        if isinstance(self.tools, Unset):
            tools = UNSET
        elif isinstance(self.tools, list):
            tools = []
            for tools_type_0_item_data in self.tools:
                tools_type_0_item = tools_type_0_item_data.to_dict()
                tools.append(tools_type_0_item)

        else:
            tools = self.tools

        user: None | str | Unset
        if isinstance(self.user, Unset):
            user = UNSET
        else:
            user = self.user

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "messages": messages,
                "model": model,
            }
        )
        if max_completion_tokens is not UNSET:
            field_dict["max_completion_tokens"] = max_completion_tokens
        if max_tokens is not UNSET:
            field_dict["max_tokens"] = max_tokens
        if stream is not UNSET:
            field_dict["stream"] = stream
        if stream_options is not UNSET:
            field_dict["stream_options"] = stream_options
        if temperature is not UNSET:
            field_dict["temperature"] = temperature
        if tool_choice is not UNSET:
            field_dict["tool_choice"] = tool_choice
        if tools is not UNSET:
            field_dict["tools"] = tools
        if user is not UNSET:
            field_dict["user"] = user

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.open_ai_chat_request_messages_item import OpenAIChatRequestMessagesItem  # noqa: PLC0415
        from ..models.open_ai_chat_request_stream_options_type_0 import OpenAIChatRequestStreamOptionsType0  # noqa: PLC0415
        from ..models.open_ai_chat_request_tool_choice_type_1 import OpenAIChatRequestToolChoiceType1  # noqa: PLC0415
        from ..models.open_ai_chat_request_tools_type_0_item import OpenAIChatRequestToolsType0Item  # noqa: PLC0415

        d = dict(src_dict)
        messages = []
        _messages = d.pop("messages")
        for messages_item_data in _messages:
            messages_item = OpenAIChatRequestMessagesItem.from_dict(messages_item_data)

            messages.append(messages_item)

        model = d.pop("model")

        def _parse_max_completion_tokens(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        max_completion_tokens = _parse_max_completion_tokens(d.pop("max_completion_tokens", UNSET))

        def _parse_max_tokens(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        max_tokens = _parse_max_tokens(d.pop("max_tokens", UNSET))

        stream = d.pop("stream", UNSET)

        def _parse_stream_options(data: object) -> None | OpenAIChatRequestStreamOptionsType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                stream_options_type_0 = OpenAIChatRequestStreamOptionsType0.from_dict(data)

                return stream_options_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | OpenAIChatRequestStreamOptionsType0 | Unset, data)

        stream_options = _parse_stream_options(d.pop("stream_options", UNSET))

        def _parse_temperature(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        temperature = _parse_temperature(d.pop("temperature", UNSET))

        def _parse_tool_choice(data: object) -> None | OpenAIChatRequestToolChoiceType1 | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                tool_choice_type_1 = OpenAIChatRequestToolChoiceType1.from_dict(data)

                return tool_choice_type_1
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | OpenAIChatRequestToolChoiceType1 | str | Unset, data)

        tool_choice = _parse_tool_choice(d.pop("tool_choice", UNSET))

        def _parse_tools(data: object) -> list[OpenAIChatRequestToolsType0Item] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                tools_type_0 = []
                _tools_type_0 = data
                for tools_type_0_item_data in _tools_type_0:
                    tools_type_0_item = OpenAIChatRequestToolsType0Item.from_dict(tools_type_0_item_data)

                    tools_type_0.append(tools_type_0_item)

                return tools_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[OpenAIChatRequestToolsType0Item] | None | Unset, data)

        tools = _parse_tools(d.pop("tools", UNSET))

        def _parse_user(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        user = _parse_user(d.pop("user", UNSET))

        open_ai_chat_request = cls(
            messages=messages,
            model=model,
            max_completion_tokens=max_completion_tokens,
            max_tokens=max_tokens,
            stream=stream,
            stream_options=stream_options,
            temperature=temperature,
            tool_choice=tool_choice,
            tools=tools,
            user=user,
        )

        open_ai_chat_request.additional_properties = d
        return open_ai_chat_request

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
