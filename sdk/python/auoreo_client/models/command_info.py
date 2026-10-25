from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.command_info_handler import CommandInfoHandler

T = TypeVar("T", bound="CommandInfo")


@_attrs_define
class CommandInfo:
    """
    Attributes:
        args_hint (str):
        description (str):
        handler (CommandInfoHandler):
        name (str):
    """

    args_hint: str
    description: str
    handler: CommandInfoHandler
    name: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        args_hint = self.args_hint

        description = self.description

        handler = self.handler.value

        name = self.name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "args_hint": args_hint,
                "description": description,
                "handler": handler,
                "name": name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        args_hint = d.pop("args_hint")

        description = d.pop("description")

        handler = CommandInfoHandler(d.pop("handler"))

        name = d.pop("name")

        command_info = cls(
            args_hint=args_hint,
            description=description,
            handler=handler,
            name=name,
        )

        command_info.additional_properties = d
        return command_info

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
