from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.message_out_tool_calls_type_0_item import MessageOutToolCallsType0Item


T = TypeVar("T", bound="MessageOut")


@_attrs_define
class MessageOut:
    """
    Attributes:
        content (None | str):
        idx (int):
        role (str):
        name (None | str | Unset):
        tool_call_id (None | str | Unset):
        tool_calls (list[MessageOutToolCallsType0Item] | None | Unset):
    """

    content: None | str
    idx: int
    role: str
    name: None | str | Unset = UNSET
    tool_call_id: None | str | Unset = UNSET
    tool_calls: list[MessageOutToolCallsType0Item] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        content: None | str
        content = self.content

        idx = self.idx

        role = self.role

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        tool_call_id: None | str | Unset
        if isinstance(self.tool_call_id, Unset):
            tool_call_id = UNSET
        else:
            tool_call_id = self.tool_call_id

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
                "idx": idx,
                "role": role,
            }
        )
        if name is not UNSET:
            field_dict["name"] = name
        if tool_call_id is not UNSET:
            field_dict["tool_call_id"] = tool_call_id
        if tool_calls is not UNSET:
            field_dict["tool_calls"] = tool_calls

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.message_out_tool_calls_type_0_item import MessageOutToolCallsType0Item  # noqa: PLC0415

        d = dict(src_dict)

        def _parse_content(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        content = _parse_content(d.pop("content"))

        idx = d.pop("idx")

        role = d.pop("role")

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_tool_call_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tool_call_id = _parse_tool_call_id(d.pop("tool_call_id", UNSET))

        def _parse_tool_calls(data: object) -> list[MessageOutToolCallsType0Item] | None | Unset:
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
                    tool_calls_type_0_item = MessageOutToolCallsType0Item.from_dict(
                        tool_calls_type_0_item_data
                    )

                    tool_calls_type_0.append(tool_calls_type_0_item)

                return tool_calls_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[MessageOutToolCallsType0Item] | None | Unset, data)

        tool_calls = _parse_tool_calls(d.pop("tool_calls", UNSET))

        message_out = cls(
            content=content,
            idx=idx,
            role=role,
            name=name,
            tool_call_id=tool_call_id,
            tool_calls=tool_calls,
        )

        message_out.additional_properties = d
        return message_out

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
