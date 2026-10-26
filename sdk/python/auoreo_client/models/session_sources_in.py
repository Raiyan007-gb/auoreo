from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="SessionSourcesIn")


@_attrs_define
class SessionSourcesIn:
    """
    Attributes:
        disabled (list[str]): Sources switched off for this session (replaces the list)
    """

    disabled: list[str]

    def to_dict(self) -> dict[str, Any]:
        disabled = self.disabled

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "disabled": disabled,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        disabled = cast(list[str], d.pop("disabled"))

        session_sources_in = cls(
            disabled=disabled,
        )

        return session_sources_in
