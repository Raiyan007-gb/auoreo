from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="RtcCapabilities")


@_attrs_define
class RtcCapabilities:
    """What a browser call client may ask for in `POST /api/rtc/offer`.

    Attributes:
        available (bool): aiortc is installed (else offers answer 501)
        speak (bool): An outbound speech track is attached unless the offer says `speak: false`
        teleop_input_enabled (bool): `settings.teleop_input_enabled`: offers may set `input: true` (`auoreo-input`
            channel)
        voice_enabled (bool): `settings.voice_enabled`: live transcription runs
    """

    available: bool
    speak: bool
    teleop_input_enabled: bool
    voice_enabled: bool
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        available = self.available

        speak = self.speak

        teleop_input_enabled = self.teleop_input_enabled

        voice_enabled = self.voice_enabled

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "available": available,
                "speak": speak,
                "teleop_input_enabled": teleop_input_enabled,
                "voice_enabled": voice_enabled,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        available = d.pop("available")

        speak = d.pop("speak")

        teleop_input_enabled = d.pop("teleop_input_enabled")

        voice_enabled = d.pop("voice_enabled")

        rtc_capabilities = cls(
            available=available,
            speak=speak,
            teleop_input_enabled=teleop_input_enabled,
            voice_enabled=voice_enabled,
        )

        rtc_capabilities.additional_properties = d
        return rtc_capabilities

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
