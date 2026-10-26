from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OAuthSettled")


@_attrs_define
class OAuthSettled:
    """
    Attributes:
        connector (str):
        settled (bool): A refresh token is stored as `oauth/<connector>`
        flow (str | Unset):  Default: ''.
    """

    connector: str
    settled: bool
    flow: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connector = self.connector

        settled = self.settled

        flow = self.flow

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connector": connector,
                "settled": settled,
            }
        )
        if flow is not UNSET:
            field_dict["flow"] = flow

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        connector = d.pop("connector")

        settled = d.pop("settled")

        flow = d.pop("flow", UNSET)

        o_auth_settled = cls(
            connector=connector,
            settled=settled,
            flow=flow,
        )

        o_auth_settled.additional_properties = d
        return o_auth_settled

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
