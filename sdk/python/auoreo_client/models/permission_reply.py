from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.permission_reply_reply import PermissionReplyReply
from ..types import UNSET, Unset

T = TypeVar("T", bound="PermissionReply")


@_attrs_define
class PermissionReply:
    """
    Attributes:
        reply (PermissionReplyReply):
        message (None | str | Unset):
    """

    reply: PermissionReplyReply
    message: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        reply = self.reply.value

        message: None | str | Unset
        if isinstance(self.message, Unset):
            message = UNSET
        else:
            message = self.message

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "reply": reply,
            }
        )
        if message is not UNSET:
            field_dict["message"] = message

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        reply = PermissionReplyReply(d.pop("reply"))

        def _parse_message(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        message = _parse_message(d.pop("message", UNSET))

        permission_reply = cls(
            reply=reply,
            message=message,
        )

        return permission_reply
