from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.open_ai_tool_call_function import OpenAIToolCallFunction


T = TypeVar("T", bound="OpenAIToolCall")


@_attrs_define
class OpenAIToolCall:
    """
    Attributes:
        function (OpenAIToolCallFunction):
        id (str):
        type_ (Literal['function'] | Unset):  Default: 'function'.
    """

    function: OpenAIToolCallFunction
    id: str
    type_: Literal["function"] | Unset = "function"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        function = self.function.to_dict()

        id = self.id

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "function": function,
                "id": id,
            }
        )
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.open_ai_tool_call_function import OpenAIToolCallFunction  # noqa: PLC0415

        d = dict(src_dict)
        function = OpenAIToolCallFunction.from_dict(d.pop("function"))

        id = d.pop("id")

        type_ = cast(Literal["function"] | Unset, d.pop("type", UNSET))
        if type_ != "function" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'function', got '{type_}'")

        open_ai_tool_call = cls(
            function=function,
            id=id,
            type_=type_,
        )

        open_ai_tool_call.additional_properties = d
        return open_ai_tool_call

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
