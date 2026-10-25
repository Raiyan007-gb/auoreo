from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.hook_ack_status import HookAckStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="HookAck")


@_attrs_define
class HookAck:
    """
    Attributes:
        status (HookAckStatus):
        delivery_id (int | None | Unset):
        event (None | str | Unset):
        route (None | str | Unset):
    """

    status: HookAckStatus
    delivery_id: int | None | Unset = UNSET
    event: None | str | Unset = UNSET
    route: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        status = self.status.value

        delivery_id: int | None | Unset
        if isinstance(self.delivery_id, Unset):
            delivery_id = UNSET
        else:
            delivery_id = self.delivery_id

        event: None | str | Unset
        if isinstance(self.event, Unset):
            event = UNSET
        else:
            event = self.event

        route: None | str | Unset
        if isinstance(self.route, Unset):
            route = UNSET
        else:
            route = self.route

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "status": status,
            }
        )
        if delivery_id is not UNSET:
            field_dict["delivery_id"] = delivery_id
        if event is not UNSET:
            field_dict["event"] = event
        if route is not UNSET:
            field_dict["route"] = route

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        status = HookAckStatus(d.pop("status"))

        def _parse_delivery_id(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        delivery_id = _parse_delivery_id(d.pop("delivery_id", UNSET))

        def _parse_event(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        event = _parse_event(d.pop("event", UNSET))

        def _parse_route(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        route = _parse_route(d.pop("route", UNSET))

        hook_ack = cls(
            status=status,
            delivery_id=delivery_id,
            event=event,
            route=route,
        )

        hook_ack.additional_properties = d
        return hook_ack

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
