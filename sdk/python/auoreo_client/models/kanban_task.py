from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.kanban_task_status import KanbanTaskStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="KanbanTask")


@_attrs_define
class KanbanTask:
    """
    Attributes:
        board (str):
        body (str):
        created_at (datetime.datetime):
        id (int):
        status (KanbanTaskStatus):
        title (str):
        updated_at (datetime.datetime):
        failures (int | Unset):  Default: 0.
        heartbeat_at (datetime.datetime | None | Unset):
        session_id (None | str | Unset):
        worker (None | str | Unset):
    """

    board: str
    body: str
    created_at: datetime.datetime
    id: int
    status: KanbanTaskStatus
    title: str
    updated_at: datetime.datetime
    failures: int | Unset = 0
    heartbeat_at: datetime.datetime | None | Unset = UNSET
    session_id: None | str | Unset = UNSET
    worker: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        board = self.board

        body = self.body

        created_at = self.created_at.isoformat()

        id = self.id

        status = self.status.value

        title = self.title

        updated_at = self.updated_at.isoformat()

        failures = self.failures

        heartbeat_at: None | str | Unset
        if isinstance(self.heartbeat_at, Unset):
            heartbeat_at = UNSET
        elif isinstance(self.heartbeat_at, datetime.datetime):
            heartbeat_at = self.heartbeat_at.isoformat()
        else:
            heartbeat_at = self.heartbeat_at

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        worker: None | str | Unset
        if isinstance(self.worker, Unset):
            worker = UNSET
        else:
            worker = self.worker

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "board": board,
                "body": body,
                "created_at": created_at,
                "id": id,
                "status": status,
                "title": title,
                "updated_at": updated_at,
            }
        )
        if failures is not UNSET:
            field_dict["failures"] = failures
        if heartbeat_at is not UNSET:
            field_dict["heartbeat_at"] = heartbeat_at
        if session_id is not UNSET:
            field_dict["session_id"] = session_id
        if worker is not UNSET:
            field_dict["worker"] = worker

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        board = d.pop("board")

        body = d.pop("body")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        status = KanbanTaskStatus(d.pop("status"))

        title = d.pop("title")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        failures = d.pop("failures", UNSET)

        def _parse_heartbeat_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                heartbeat_at_type_0 = datetime.datetime.fromisoformat(data)

                return heartbeat_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        heartbeat_at = _parse_heartbeat_at(d.pop("heartbeat_at", UNSET))

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        def _parse_worker(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        worker = _parse_worker(d.pop("worker", UNSET))

        kanban_task = cls(
            board=board,
            body=body,
            created_at=created_at,
            id=id,
            status=status,
            title=title,
            updated_at=updated_at,
            failures=failures,
            heartbeat_at=heartbeat_at,
            session_id=session_id,
            worker=worker,
        )

        kanban_task.additional_properties = d
        return kanban_task

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
