from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="PendingPermission")


@_attrs_define
class PendingPermission:
    """
    Attributes:
        detail (str):
        pattern (str):
        permission (str):
        request_id (str):
        session_id (str):
        tool (str):
        one_shot (bool | Unset): No `always`: single-use (e.g. sandbox escalation). Default: False.
        risk (str | Unset):  Default: 'medium'.
    """

    detail: str
    pattern: str
    permission: str
    request_id: str
    session_id: str
    tool: str
    one_shot: bool | Unset = False
    risk: str | Unset = "medium"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        detail = self.detail

        pattern = self.pattern

        permission = self.permission

        request_id = self.request_id

        session_id = self.session_id

        tool = self.tool

        one_shot = self.one_shot

        risk = self.risk

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "detail": detail,
                "pattern": pattern,
                "permission": permission,
                "request_id": request_id,
                "session_id": session_id,
                "tool": tool,
            }
        )
        if one_shot is not UNSET:
            field_dict["one_shot"] = one_shot
        if risk is not UNSET:
            field_dict["risk"] = risk

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        detail = d.pop("detail")

        pattern = d.pop("pattern")

        permission = d.pop("permission")

        request_id = d.pop("request_id")

        session_id = d.pop("session_id")

        tool = d.pop("tool")

        one_shot = d.pop("one_shot", UNSET)

        risk = d.pop("risk", UNSET)

        pending_permission = cls(
            detail=detail,
            pattern=pattern,
            permission=permission,
            request_id=request_id,
            session_id=session_id,
            tool=tool,
            one_shot=one_shot,
            risk=risk,
        )

        pending_permission.additional_properties = d
        return pending_permission

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
