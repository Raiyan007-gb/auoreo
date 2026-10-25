from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.voice_backend_status import VoiceBackendStatus


T = TypeVar("T", bound="VoiceStatus")


@_attrs_define
class VoiceStatus:
    """
    Attributes:
        backends (list[VoiceBackendStatus]):
        enabled (bool):
    """

    backends: list[VoiceBackendStatus]
    enabled: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        backends = []
        for backends_item_data in self.backends:
            backends_item = backends_item_data.to_dict()
            backends.append(backends_item)

        enabled = self.enabled

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "backends": backends,
                "enabled": enabled,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.voice_backend_status import VoiceBackendStatus  # noqa: PLC0415

        d = dict(src_dict)
        backends = []
        _backends = d.pop("backends")
        for backends_item_data in _backends:
            backends_item = VoiceBackendStatus.from_dict(backends_item_data)

            backends.append(backends_item)

        enabled = d.pop("enabled")

        voice_status = cls(
            backends=backends,
            enabled=enabled,
        )

        voice_status.additional_properties = d
        return voice_status

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
