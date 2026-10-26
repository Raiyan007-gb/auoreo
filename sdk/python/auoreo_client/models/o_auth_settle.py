from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="OAuthSettle")


@_attrs_define
class OAuthSettle:
    """
    Attributes:
        flow (str | Unset): Flow that signed in (loopback/device/manual) Default: ''.
    """

    flow: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        flow = self.flow

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if flow is not UNSET:
            field_dict["flow"] = flow

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        flow = d.pop("flow", UNSET)

        o_auth_settle = cls(
            flow=flow,
        )

        return o_auth_settle
