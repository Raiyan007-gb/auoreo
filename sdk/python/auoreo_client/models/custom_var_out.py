from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CustomVarOut")


@_attrs_define
class CustomVarOut:
    """
    Attributes:
        expose_to_agent (bool):
        expose_to_shell (bool):
        is_set (bool):
        name (str):
        secret (bool):
        updated_at (None | str | Unset):
        value (None | str | Unset): Plain variables only; null for secrets
    """

    expose_to_agent: bool
    expose_to_shell: bool
    is_set: bool
    name: str
    secret: bool
    updated_at: None | str | Unset = UNSET
    value: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        expose_to_agent = self.expose_to_agent

        expose_to_shell = self.expose_to_shell

        is_set = self.is_set

        name = self.name

        secret = self.secret

        updated_at: None | str | Unset
        if isinstance(self.updated_at, Unset):
            updated_at = UNSET
        else:
            updated_at = self.updated_at

        value: None | str | Unset
        if isinstance(self.value, Unset):
            value = UNSET
        else:
            value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "expose_to_agent": expose_to_agent,
                "expose_to_shell": expose_to_shell,
                "is_set": is_set,
                "name": name,
                "secret": secret,
            }
        )
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at
        if value is not UNSET:
            field_dict["value"] = value

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expose_to_agent = d.pop("expose_to_agent")

        expose_to_shell = d.pop("expose_to_shell")

        is_set = d.pop("is_set")

        name = d.pop("name")

        secret = d.pop("secret")

        def _parse_updated_at(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        updated_at = _parse_updated_at(d.pop("updated_at", UNSET))

        def _parse_value(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        value = _parse_value(d.pop("value", UNSET))

        custom_var_out = cls(
            expose_to_agent=expose_to_agent,
            expose_to_shell=expose_to_shell,
            is_set=is_set,
            name=name,
            secret=secret,
            updated_at=updated_at,
            value=value,
        )

        custom_var_out.additional_properties = d
        return custom_var_out

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
