from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.live_kit_agent_dispatch import LiveKitAgentDispatch


T = TypeVar("T", bound="LiveKitRoomConfig")


@_attrs_define
class LiveKitRoomConfig:
    """Only explicit agent dispatch is allowed; any other room option is refused (400).

    Attributes:
        agents (list[LiveKitAgentDispatch] | Unset):
    """

    agents: list[LiveKitAgentDispatch] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        agents: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.agents, Unset):
            agents = []
            for agents_item_data in self.agents:
                agents_item = agents_item_data.to_dict()
                agents.append(agents_item)

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if agents is not UNSET:
            field_dict["agents"] = agents

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.live_kit_agent_dispatch import LiveKitAgentDispatch  # noqa: PLC0415

        d = dict(src_dict)
        _agents = d.pop("agents", UNSET)
        agents: list[LiveKitAgentDispatch] | Unset = UNSET
        if _agents is not UNSET:
            agents = []
            for agents_item_data in _agents:
                agents_item = LiveKitAgentDispatch.from_dict(agents_item_data)

                agents.append(agents_item)

        live_kit_room_config = cls(
            agents=agents,
        )

        return live_kit_room_config
