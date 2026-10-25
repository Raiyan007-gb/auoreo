from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="EventsTailRequest")


@_attrs_define
class EventsTailRequest:
    """
    Attributes:
        after_seq (int | None | Unset):
        channels (list[str] | None | Unset):
        limit (int | Unset):  Default: 50.
    """

    after_seq: int | None | Unset = UNSET
    channels: list[str] | None | Unset = UNSET
    limit: int | Unset = 50
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        after_seq: int | None | Unset
        if isinstance(self.after_seq, Unset):
            after_seq = UNSET
        else:
            after_seq = self.after_seq

        channels: list[str] | None | Unset
        if isinstance(self.channels, Unset):
            channels = UNSET
        elif isinstance(self.channels, list):
            channels = self.channels

        else:
            channels = self.channels

        limit = self.limit

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if after_seq is not UNSET:
            field_dict["after_seq"] = after_seq
        if channels is not UNSET:
            field_dict["channels"] = channels
        if limit is not UNSET:
            field_dict["limit"] = limit

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_after_seq(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        after_seq = _parse_after_seq(d.pop("after_seq", UNSET))

        def _parse_channels(data: object) -> list[str] | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                channels_type_0 = cast(list[str], data)

                return channels_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | None | Unset, data)

        channels = _parse_channels(d.pop("channels", UNSET))

        limit = d.pop("limit", UNSET)

        events_tail_request = cls(
            after_seq=after_seq,
            channels=channels,
            limit=limit,
        )

        events_tail_request.additional_properties = d
        return events_tail_request

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
