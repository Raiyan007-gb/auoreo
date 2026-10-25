from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="KanbanCreate")


@_attrs_define
class KanbanCreate:
    """
    Attributes:
        title (str):
        board (str | Unset):  Default: 'default'.
        body (str | Unset):  Default: ''.
    """

    title: str
    board: str | Unset = "default"
    body: str | Unset = ""

    def to_dict(self) -> dict[str, Any]:
        title = self.title

        board = self.board

        body = self.body

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "title": title,
            }
        )
        if board is not UNSET:
            field_dict["board"] = board
        if body is not UNSET:
            field_dict["body"] = body

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        title = d.pop("title")

        board = d.pop("board", UNSET)

        body = d.pop("body", UNSET)

        kanban_create = cls(
            title=title,
            board=board,
            body=body,
        )

        return kanban_create
