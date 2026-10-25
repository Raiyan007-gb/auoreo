from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.server_event_data import ServerEventData


T = TypeVar("T", bound="ServerEvent")


@_attrs_define
class ServerEvent:
    """
    Attributes:
        aggregate_id (str): Session id, or 'server'
        aggregate_seq (int): Monotonic sequence within the aggregate
        created_at (datetime.datetime):
        data (ServerEventData):
        id (str): str(seq) — use as SSE id / Last-Event-ID
        seq (int): Global monotonic sequence (DB autoincrement)
        type_ (str): e.g. run.started, message.delta, tool.completed
    """

    aggregate_id: str
    aggregate_seq: int
    created_at: datetime.datetime
    data: ServerEventData
    id: str
    seq: int
    type_: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        aggregate_id = self.aggregate_id

        aggregate_seq = self.aggregate_seq

        created_at = self.created_at.isoformat()

        data = self.data.to_dict()

        id = self.id

        seq = self.seq

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "aggregate_id": aggregate_id,
                "aggregate_seq": aggregate_seq,
                "created_at": created_at,
                "data": data,
                "id": id,
                "seq": seq,
                "type": type_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.server_event_data import ServerEventData  # noqa: PLC0415

        d = dict(src_dict)
        aggregate_id = d.pop("aggregate_id")

        aggregate_seq = d.pop("aggregate_seq")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        data = ServerEventData.from_dict(d.pop("data"))

        id = d.pop("id")

        seq = d.pop("seq")

        type_ = d.pop("type")

        server_event = cls(
            aggregate_id=aggregate_id,
            aggregate_seq=aggregate_seq,
            created_at=created_at,
            data=data,
            id=id,
            seq=seq,
            type_=type_,
        )

        server_event.additional_properties = d
        return server_event

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
