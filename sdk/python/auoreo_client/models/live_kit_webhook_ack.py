from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.live_kit_webhook_ack_extra import LiveKitWebhookAckExtra


T = TypeVar("T", bound="LiveKitWebhookAck")


@_attrs_define
class LiveKitWebhookAck:
    """
    Attributes:
        status (str): `ok` or `duplicate`
        event (None | str | Unset):
        extra (LiveKitWebhookAckExtra | Unset):
        id (None | str | Unset):
    """

    status: str
    event: None | str | Unset = UNSET
    extra: LiveKitWebhookAckExtra | Unset = UNSET
    id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        status = self.status

        event: None | str | Unset
        if isinstance(self.event, Unset):
            event = UNSET
        else:
            event = self.event

        extra: dict[str, Any] | Unset = UNSET
        if not isinstance(self.extra, Unset):
            extra = self.extra.to_dict()

        id: None | str | Unset
        if isinstance(self.id, Unset):
            id = UNSET
        else:
            id = self.id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "status": status,
            }
        )
        if event is not UNSET:
            field_dict["event"] = event
        if extra is not UNSET:
            field_dict["extra"] = extra
        if id is not UNSET:
            field_dict["id"] = id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.live_kit_webhook_ack_extra import LiveKitWebhookAckExtra  # noqa: PLC0415

        d = dict(src_dict)
        status = d.pop("status")

        def _parse_event(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        event = _parse_event(d.pop("event", UNSET))

        _extra = d.pop("extra", UNSET)
        extra: LiveKitWebhookAckExtra | Unset
        if isinstance(_extra, Unset):
            extra = UNSET
        else:
            extra = LiveKitWebhookAckExtra.from_dict(_extra)

        def _parse_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        id = _parse_id(d.pop("id", UNSET))

        live_kit_webhook_ack = cls(
            status=status,
            event=event,
            extra=extra,
            id=id,
        )

        live_kit_webhook_ack.additional_properties = d
        return live_kit_webhook_ack

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
