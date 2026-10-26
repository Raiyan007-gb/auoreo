from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="EnvSecretRotate")


@_attrs_define
class EnvSecretRotate:
    """
    Attributes:
        value (str):
        grace_s (int | None | Unset):
    """

    value: str
    grace_s: int | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = self.value

        grace_s: int | None | Unset
        if isinstance(self.grace_s, Unset):
            grace_s = UNSET
        else:
            grace_s = self.grace_s

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "value": value,
            }
        )
        if grace_s is not UNSET:
            field_dict["grace_s"] = grace_s

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        value = d.pop("value")

        def _parse_grace_s(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        grace_s = _parse_grace_s(d.pop("grace_s", UNSET))

        env_secret_rotate = cls(
            value=value,
            grace_s=grace_s,
        )

        env_secret_rotate.additional_properties = d
        return env_secret_rotate

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
