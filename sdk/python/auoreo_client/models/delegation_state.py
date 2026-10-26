from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="DelegationState")


@_attrs_define
class DelegationState:
    """
    Attributes:
        paused (bool): True: new spawns are refused (list/steer/stop still work)
        queued (int): Children waiting for a slot
        running (int): Children running right now (process-wide)
        source (str): settings | api
    """

    paused: bool
    queued: int
    running: int
    source: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        paused = self.paused

        queued = self.queued

        running = self.running

        source = self.source

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "paused": paused,
                "queued": queued,
                "running": running,
                "source": source,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        paused = d.pop("paused")

        queued = d.pop("queued")

        running = d.pop("running")

        source = d.pop("source")

        delegation_state = cls(
            paused=paused,
            queued=queued,
            running=running,
            source=source,
        )

        delegation_state.additional_properties = d
        return delegation_state

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
