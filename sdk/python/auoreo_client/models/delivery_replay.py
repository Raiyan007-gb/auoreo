from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.delivery_info import DeliveryInfo


T = TypeVar("T", bound="DeliveryReplay")


@_attrs_define
class DeliveryReplay:
    """
    Attributes:
        dry_run (bool):
        prompt (str):
        delivery (DeliveryInfo | None | Unset): The new pending row (not on dry run)
    """

    dry_run: bool
    prompt: str
    delivery: DeliveryInfo | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.delivery_info import DeliveryInfo  # noqa: PLC0415

        dry_run = self.dry_run

        prompt = self.prompt

        delivery: dict[str, Any] | None | Unset
        if isinstance(self.delivery, Unset):
            delivery = UNSET
        elif isinstance(self.delivery, DeliveryInfo):
            delivery = self.delivery.to_dict()
        else:
            delivery = self.delivery

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "dry_run": dry_run,
                "prompt": prompt,
            }
        )
        if delivery is not UNSET:
            field_dict["delivery"] = delivery

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.delivery_info import DeliveryInfo  # noqa: PLC0415

        d = dict(src_dict)
        dry_run = d.pop("dry_run")

        prompt = d.pop("prompt")

        def _parse_delivery(data: object) -> DeliveryInfo | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                delivery_type_0 = DeliveryInfo.from_dict(data)

                return delivery_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(DeliveryInfo | None | Unset, data)

        delivery = _parse_delivery(d.pop("delivery", UNSET))

        delivery_replay = cls(
            dry_run=dry_run,
            prompt=prompt,
            delivery=delivery,
        )

        delivery_replay.additional_properties = d
        return delivery_replay

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
