from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.session_source import SessionSource


T = TypeVar("T", bound="SessionSources")


@_attrs_define
class SessionSources:
    """
    Attributes:
        disabled (list[str]):
        session_id (str):
        sources (list[SessionSource]):
    """

    disabled: list[str]
    session_id: str
    sources: list[SessionSource]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        disabled = self.disabled

        session_id = self.session_id

        sources = []
        for sources_item_data in self.sources:
            sources_item = sources_item_data.to_dict()
            sources.append(sources_item)

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "disabled": disabled,
                "session_id": session_id,
                "sources": sources,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.session_source import SessionSource  # noqa: PLC0415

        d = dict(src_dict)
        disabled = cast(list[str], d.pop("disabled"))

        session_id = d.pop("session_id")

        sources = []
        _sources = d.pop("sources")
        for sources_item_data in _sources:
            sources_item = SessionSource.from_dict(sources_item_data)

            sources.append(sources_item)

        session_sources = cls(
            disabled=disabled,
            session_id=session_id,
            sources=sources,
        )

        session_sources.additional_properties = d
        return session_sources

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
