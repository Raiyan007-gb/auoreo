from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CustomVarWrite")


@_attrs_define
class CustomVarWrite:
    """
    Attributes:
        name (str):
        expose_to_agent (bool | Unset):  Default: False.
        expose_to_shell (bool | Unset):  Default: False.
        secret (bool | Unset):  Default: False.
        value (None | str | Unset): Omit on PUT to keep the value
    """

    name: str
    expose_to_agent: bool | Unset = False
    expose_to_shell: bool | Unset = False
    secret: bool | Unset = False
    value: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        expose_to_agent = self.expose_to_agent

        expose_to_shell = self.expose_to_shell

        secret = self.secret

        value: None | str | Unset
        if isinstance(self.value, Unset):
            value = UNSET
        else:
            value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
            }
        )
        if expose_to_agent is not UNSET:
            field_dict["expose_to_agent"] = expose_to_agent
        if expose_to_shell is not UNSET:
            field_dict["expose_to_shell"] = expose_to_shell
        if secret is not UNSET:
            field_dict["secret"] = secret
        if value is not UNSET:
            field_dict["value"] = value

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        expose_to_agent = d.pop("expose_to_agent", UNSET)

        expose_to_shell = d.pop("expose_to_shell", UNSET)

        secret = d.pop("secret", UNSET)

        def _parse_value(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        value = _parse_value(d.pop("value", UNSET))

        custom_var_write = cls(
            name=name,
            expose_to_agent=expose_to_agent,
            expose_to_shell=expose_to_shell,
            secret=secret,
            value=value,
        )

        custom_var_write.additional_properties = d
        return custom_var_write

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
