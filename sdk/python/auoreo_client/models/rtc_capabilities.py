from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

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
        livekit (bool | Unset): I8: the LiveKit transport is selected and `i8.livekit.web_toggle` is on (call panel
            toggle; tokens from `POST /api/livekit/token`) Default: False.
        livekit_agent (None | str | Unset): I8: agent name to dispatch (`room_config.agents`) when the LiveKit worker
            runs
    """

    available: bool
    speak: bool
    teleop_input_enabled: bool
    voice_enabled: bool
    livekit: bool | Unset = False
    livekit_agent: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        available = self.available

        speak = self.speak

        teleop_input_enabled = self.teleop_input_enabled

        voice_enabled = self.voice_enabled

        livekit = self.livekit

        livekit_agent: None | str | Unset
        if isinstance(self.livekit_agent, Unset):
            livekit_agent = UNSET
        else:
            livekit_agent = self.livekit_agent

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
        if livekit is not UNSET:
            field_dict["livekit"] = livekit
        if livekit_agent is not UNSET:
            field_dict["livekit_agent"] = livekit_agent

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        available = d.pop("available")

        speak = d.pop("speak")

        teleop_input_enabled = d.pop("teleop_input_enabled")

        voice_enabled = d.pop("voice_enabled")

        livekit = d.pop("livekit", UNSET)

        def _parse_livekit_agent(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        livekit_agent = _parse_livekit_agent(d.pop("livekit_agent", UNSET))

        rtc_capabilities = cls(
            available=available,
            speak=speak,
            teleop_input_enabled=teleop_input_enabled,
            voice_enabled=voice_enabled,
            livekit=livekit,
            livekit_agent=livekit_agent,
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
