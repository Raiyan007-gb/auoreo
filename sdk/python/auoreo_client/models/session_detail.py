from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="SessionDetail")


@_attrs_define
class SessionDetail:
    """`SessionInfo` plus lineage/archive state (fork, archive endpoints).

    Attributes:
        agent (str):
        created_at (datetime.datetime):
        id (str):
        model (None | str):
        status (str):
        title (str):
        updated_at (datetime.datetime):
        workflow (str):
        archived (bool | Unset):  Default: False.
        parent_id (None | str | Unset):
    """

    agent: str
    created_at: datetime.datetime
    id: str
    model: None | str
    status: str
    title: str
    updated_at: datetime.datetime
    workflow: str
    archived: bool | Unset = False
    parent_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        agent = self.agent

        created_at = self.created_at.isoformat()

        id = self.id

        model: None | str
        model = self.model

        status = self.status

        title = self.title

        updated_at = self.updated_at.isoformat()

        workflow = self.workflow

        archived = self.archived

        parent_id: None | str | Unset
        if isinstance(self.parent_id, Unset):
            parent_id = UNSET
        else:
            parent_id = self.parent_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "created_at": created_at,
                "id": id,
                "model": model,
                "status": status,
                "title": title,
                "updated_at": updated_at,
                "workflow": workflow,
            }
        )
        if archived is not UNSET:
            field_dict["archived"] = archived
        if parent_id is not UNSET:
            field_dict["parent_id"] = parent_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent = d.pop("agent")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        def _parse_model(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        model = _parse_model(d.pop("model"))

        status = d.pop("status")

        title = d.pop("title")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        workflow = d.pop("workflow")

        archived = d.pop("archived", UNSET)

        def _parse_parent_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        parent_id = _parse_parent_id(d.pop("parent_id", UNSET))

        session_detail = cls(
            agent=agent,
            created_at=created_at,
            id=id,
            model=model,
            status=status,
            title=title,
            updated_at=updated_at,
            workflow=workflow,
            archived=archived,
            parent_id=parent_id,
        )

        session_detail.additional_properties = d
        return session_detail

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
