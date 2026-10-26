from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.permission_reply_reply import PermissionReplyReply
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.permission_reply_updated_input_type_0 import PermissionReplyUpdatedInputType0


T = TypeVar("T", bound="PermissionReply")


@_attrs_define
class PermissionReply:
    """
    Attributes:
        reply (PermissionReplyReply):
        cascade (bool | Unset): With `once`: also approve every pending request in the same approval group (permission,
            cwd, risk tier, tool) across sessions. Default: False.
        message (None | str | Unset):
        updated_input (None | PermissionReplyUpdatedInputType0 | Unset): Allow with edited tool arguments (spec §5.2
            `allow{updatedInput}`); only for requests marked `editable`. Re-validated against the tool schema, approved
            once, audited.
    """

    reply: PermissionReplyReply
    cascade: bool | Unset = False
    message: None | str | Unset = UNSET
    updated_input: None | PermissionReplyUpdatedInputType0 | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.permission_reply_updated_input_type_0 import PermissionReplyUpdatedInputType0  # noqa: PLC0415

        reply = self.reply.value

        cascade = self.cascade

        message: None | str | Unset
        if isinstance(self.message, Unset):
            message = UNSET
        else:
            message = self.message

        updated_input: dict[str, Any] | None | Unset
        if isinstance(self.updated_input, Unset):
            updated_input = UNSET
        elif isinstance(self.updated_input, PermissionReplyUpdatedInputType0):
            updated_input = self.updated_input.to_dict()
        else:
            updated_input = self.updated_input

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "reply": reply,
            }
        )
        if cascade is not UNSET:
            field_dict["cascade"] = cascade
        if message is not UNSET:
            field_dict["message"] = message
        if updated_input is not UNSET:
            field_dict["updated_input"] = updated_input

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.permission_reply_updated_input_type_0 import PermissionReplyUpdatedInputType0  # noqa: PLC0415

        d = dict(src_dict)
        reply = PermissionReplyReply(d.pop("reply"))

        cascade = d.pop("cascade", UNSET)

        def _parse_message(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        message = _parse_message(d.pop("message", UNSET))

        def _parse_updated_input(data: object) -> None | PermissionReplyUpdatedInputType0 | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                updated_input_type_0 = PermissionReplyUpdatedInputType0.from_dict(data)

                return updated_input_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | PermissionReplyUpdatedInputType0 | Unset, data)

        updated_input = _parse_updated_input(d.pop("updated_input", UNSET))

        permission_reply = cls(
            reply=reply,
            cascade=cascade,
            message=message,
            updated_input=updated_input,
        )

        return permission_reply
