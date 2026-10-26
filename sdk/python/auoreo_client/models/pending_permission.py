from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.pending_permission_input_type_0 import PendingPermissionInputType0


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
        editable (bool | Unset): The reply may carry `updated_input`. Default: False.
        input_ (None | PendingPermissionInputType0 | Unset): Arguments for the edit form.
        justification (None | str | Unset): Why the agent asks (escalation).
        one_shot (bool | Unset): No `always`: single-use (e.g. sandbox escalation). Default: False.
        risk (str | Unset):  Default: 'medium'.
    """

    detail: str
    pattern: str
    permission: str
    request_id: str
    session_id: str
    tool: str
    editable: bool | Unset = False
    input_: None | PendingPermissionInputType0 | Unset = UNSET
    justification: None | str | Unset = UNSET
    one_shot: bool | Unset = False
    risk: str | Unset = "medium"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.pending_permission_input_type_0 import PendingPermissionInputType0  # noqa: PLC0415

        detail = self.detail

        pattern = self.pattern

        permission = self.permission

        request_id = self.request_id

        session_id = self.session_id

        tool = self.tool

        editable = self.editable

        input_: dict[str, Any] | None | Unset
        if isinstance(self.input_, Unset):
            input_ = UNSET
        elif isinstance(self.input_, PendingPermissionInputType0):
            input_ = self.input_.to_dict()
        else:
            input_ = self.input_

        justification: None | str | Unset
        if isinstance(self.justification, Unset):
            justification = UNSET
        else:
            justification = self.justification

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
        if editable is not UNSET:
            field_dict["editable"] = editable
        if input_ is not UNSET:
            field_dict["input"] = input_
        if justification is not UNSET:
            field_dict["justification"] = justification
        if one_shot is not UNSET:
            field_dict["one_shot"] = one_shot
        if risk is not UNSET:
            field_dict["risk"] = risk

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.pending_permission_input_type_0 import PendingPermissionInputType0  # noqa: PLC0415

        d = dict(src_dict)
        detail = d.pop("detail")

        pattern = d.pop("pattern")

        permission = d.pop("permission")

        request_id = d.pop("request_id")

        session_id = d.pop("session_id")

        tool = d.pop("tool")

        editable = d.pop("editable", UNSET)

        def _parse_input_(data: object) -> None | PendingPermissionInputType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                input_type_0 = PendingPermissionInputType0.from_dict(data)

                return input_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | PendingPermissionInputType0 | Unset, data)

        input_ = _parse_input_(d.pop("input", UNSET))

        def _parse_justification(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        justification = _parse_justification(d.pop("justification", UNSET))

        one_shot = d.pop("one_shot", UNSET)

        risk = d.pop("risk", UNSET)

        pending_permission = cls(
            detail=detail,
            pattern=pattern,
            permission=permission,
            request_id=request_id,
            session_id=session_id,
            tool=tool,
            editable=editable,
            input_=input_,
            justification=justification,
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
