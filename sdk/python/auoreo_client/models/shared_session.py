from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.session_detail import SessionDetail
    from ..models.shared_session_messages_item import SharedSessionMessagesItem


T = TypeVar("T", bound="SharedSession")


@_attrs_define
class SharedSession:
    """Read-only view behind a share token; secrets in messages are always redacted.

    Attributes:
        messages (list[SharedSessionMessagesItem]):
        session (SessionDetail): `SessionInfo` plus lineage/archive state (fork, archive endpoints).
    """

    messages: list[SharedSessionMessagesItem]
    session: SessionDetail
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        messages = []
        for messages_item_data in self.messages:
            messages_item = messages_item_data.to_dict()
            messages.append(messages_item)

        session = self.session.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "messages": messages,
                "session": session,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.session_detail import SessionDetail  # noqa: PLC0415
        from ..models.shared_session_messages_item import SharedSessionMessagesItem  # noqa: PLC0415

        d = dict(src_dict)
        messages = []
        _messages = d.pop("messages")
        for messages_item_data in _messages:
            messages_item = SharedSessionMessagesItem.from_dict(messages_item_data)

            messages.append(messages_item)

        session = SessionDetail.from_dict(d.pop("session"))

        shared_session = cls(
            messages=messages,
            session=session,
        )

        shared_session.additional_properties = d
        return shared_session

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
