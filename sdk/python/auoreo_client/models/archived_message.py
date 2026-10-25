from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.archived_message_tool_calls_type_0_item import ArchivedMessageToolCallsType0Item


T = TypeVar("T", bound="ArchivedMessage")


@_attrs_define
class ArchivedMessage:
    """
    Attributes:
        archived_at (datetime.datetime):
        compaction_id (str):
        content (None | str):
        idx (int):
        role (str):
        tool_call_id (None | str | Unset):
        tool_calls (list[ArchivedMessageToolCallsType0Item] | None | Unset):
    """

    archived_at: datetime.datetime
    compaction_id: str
    content: None | str
    idx: int
    role: str
    tool_call_id: None | str | Unset = UNSET
    tool_calls: list[ArchivedMessageToolCallsType0Item] | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        archived_at = self.archived_at.isoformat()

        compaction_id = self.compaction_id

        content: None | str
        content = self.content

        idx = self.idx

        role = self.role

        tool_call_id: None | str | Unset
        if isinstance(self.tool_call_id, Unset):
            tool_call_id = UNSET
        else:
            tool_call_id = self.tool_call_id

        tool_calls: list[dict[str, Any]] | None | Unset
        if isinstance(self.tool_calls, Unset):
            tool_calls = UNSET
        elif isinstance(self.tool_calls, list):
            tool_calls = []
            for tool_calls_type_0_item_data in self.tool_calls:
                tool_calls_type_0_item = tool_calls_type_0_item_data.to_dict()
                tool_calls.append(tool_calls_type_0_item)

        else:
            tool_calls = self.tool_calls

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "archived_at": archived_at,
                "compaction_id": compaction_id,
                "content": content,
                "idx": idx,
                "role": role,
            }
        )
        if tool_call_id is not UNSET:
            field_dict["tool_call_id"] = tool_call_id
        if tool_calls is not UNSET:
            field_dict["tool_calls"] = tool_calls

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.archived_message_tool_calls_type_0_item import ArchivedMessageToolCallsType0Item  # noqa: PLC0415

        d = dict(src_dict)
        archived_at = datetime.datetime.fromisoformat(d.pop("archived_at"))

        compaction_id = d.pop("compaction_id")

        def _parse_content(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        content = _parse_content(d.pop("content"))

        idx = d.pop("idx")

        role = d.pop("role")

        def _parse_tool_call_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tool_call_id = _parse_tool_call_id(d.pop("tool_call_id", UNSET))

        def _parse_tool_calls(data: object) -> list[ArchivedMessageToolCallsType0Item] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                tool_calls_type_0 = []
                _tool_calls_type_0 = data
                for tool_calls_type_0_item_data in _tool_calls_type_0:
                    tool_calls_type_0_item = ArchivedMessageToolCallsType0Item.from_dict(
                        tool_calls_type_0_item_data
                    )

                    tool_calls_type_0.append(tool_calls_type_0_item)

                return tool_calls_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[ArchivedMessageToolCallsType0Item] | None | Unset, data)

        tool_calls = _parse_tool_calls(d.pop("tool_calls", UNSET))

        archived_message = cls(
            archived_at=archived_at,
            compaction_id=compaction_id,
            content=content,
            idx=idx,
            role=role,
            tool_call_id=tool_call_id,
            tool_calls=tool_calls,
        )

        archived_message.additional_properties = d
        return archived_message

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
