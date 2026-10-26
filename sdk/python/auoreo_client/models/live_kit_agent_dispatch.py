from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="LiveKitAgentDispatch")


@_attrs_define
class LiveKitAgentDispatch:
    """
    Attributes:
        agent_name (str):
        metadata (None | str | Unset):
    """

    agent_name: str
    metadata: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        agent_name = self.agent_name

        metadata: None | str | Unset
        if isinstance(self.metadata, Unset):
            metadata = UNSET
        else:
            metadata = self.metadata

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent_name": agent_name,
            }
        )
        if metadata is not UNSET:
            field_dict["metadata"] = metadata

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent_name = d.pop("agent_name")

        def _parse_metadata(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        metadata = _parse_metadata(d.pop("metadata", UNSET))

        live_kit_agent_dispatch = cls(
            agent_name=agent_name,
            metadata=metadata,
        )

        return live_kit_agent_dispatch
