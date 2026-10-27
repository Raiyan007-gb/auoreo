from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.dotenv_move_to import DotenvMoveTo
from ..types import UNSET, Unset

T = TypeVar("T", bound="DotenvMove")


@_attrs_define
class DotenvMove:
    """
    Attributes:
        name (str):
        to (DotenvMoveTo):
        file (str | Unset): The `.env` file the name came from Default: ''.
        provider (None | str | Unset): Provider connection this key creates
        reason (None | str | Unset):
    """

    name: str
    to: DotenvMoveTo
    file: str | Unset = ""
    provider: None | str | Unset = UNSET
    reason: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        to = self.to.value

        file = self.file

        provider: None | str | Unset
        if isinstance(self.provider, Unset):
            provider = UNSET
        else:
            provider = self.provider

        reason: None | str | Unset
        if isinstance(self.reason, Unset):
            reason = UNSET
        else:
            reason = self.reason

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "to": to,
            }
        )
        if file is not UNSET:
            field_dict["file"] = file
        if provider is not UNSET:
            field_dict["provider"] = provider
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        to = DotenvMoveTo(d.pop("to"))

        file = d.pop("file", UNSET)

        def _parse_provider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider = _parse_provider(d.pop("provider", UNSET))

        def _parse_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        reason = _parse_reason(d.pop("reason", UNSET))

        dotenv_move = cls(
            name=name,
            to=to,
            file=file,
            provider=provider,
            reason=reason,
        )

        dotenv_move.additional_properties = d
        return dotenv_move

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
