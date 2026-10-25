from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="SessionContext")


@_attrs_define
class SessionContext:
    """Context occupancy of a session's next request (the `ctx_viz` gauge).

    `pressure` is the token count of the assembled request (system prompt + history) as the
    compaction meter counts it; `tools_tokens` is the tool-schema overhead sent alongside.

        Attributes:
            auto_compact_at (int): Pressure at which automatic compaction runs
            context_window (int):
            messages_tokens (int):
            model (str):
            pressure (int):
            ratio (float): `(pressure + tools_tokens) / context_window`, 0..1+
            session_id (str):
            system_tokens (int):
            tools_tokens (int):
    """

    auto_compact_at: int
    context_window: int
    messages_tokens: int
    model: str
    pressure: int
    ratio: float
    session_id: str
    system_tokens: int
    tools_tokens: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        auto_compact_at = self.auto_compact_at

        context_window = self.context_window

        messages_tokens = self.messages_tokens

        model = self.model

        pressure = self.pressure

        ratio = self.ratio

        session_id = self.session_id

        system_tokens = self.system_tokens

        tools_tokens = self.tools_tokens

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "auto_compact_at": auto_compact_at,
                "context_window": context_window,
                "messages_tokens": messages_tokens,
                "model": model,
                "pressure": pressure,
                "ratio": ratio,
                "session_id": session_id,
                "system_tokens": system_tokens,
                "tools_tokens": tools_tokens,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        auto_compact_at = d.pop("auto_compact_at")

        context_window = d.pop("context_window")

        messages_tokens = d.pop("messages_tokens")

        model = d.pop("model")

        pressure = d.pop("pressure")

        ratio = d.pop("ratio")

        session_id = d.pop("session_id")

        system_tokens = d.pop("system_tokens")

        tools_tokens = d.pop("tools_tokens")

        session_context = cls(
            auto_compact_at=auto_compact_at,
            context_window=context_window,
            messages_tokens=messages_tokens,
            model=model,
            pressure=pressure,
            ratio=ratio,
            session_id=session_id,
            system_tokens=system_tokens,
            tools_tokens=tools_tokens,
        )

        session_context.additional_properties = d
        return session_context

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
