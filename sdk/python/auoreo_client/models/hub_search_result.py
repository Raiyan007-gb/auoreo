from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.hub_search_hit import HubSearchHit


T = TypeVar("T", bound="HubSearchResult")


@_attrs_define
class HubSearchResult:
    """
    Attributes:
        hits (list[HubSearchHit]):
        query (str):
        taps (list[str]):
        errors (list[str] | Unset):
    """

    hits: list[HubSearchHit]
    query: str
    taps: list[str]
    errors: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        hits = []
        for hits_item_data in self.hits:
            hits_item = hits_item_data.to_dict()
            hits.append(hits_item)

        query = self.query

        taps = self.taps

        errors: list[str] | Unset = UNSET
        if not isinstance(self.errors, Unset):
            errors = self.errors

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "hits": hits,
                "query": query,
                "taps": taps,
            }
        )
        if errors is not UNSET:
            field_dict["errors"] = errors

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.hub_search_hit import HubSearchHit  # noqa: PLC0415

        d = dict(src_dict)
        hits = []
        _hits = d.pop("hits")
        for hits_item_data in _hits:
            hits_item = HubSearchHit.from_dict(hits_item_data)

            hits.append(hits_item)

        query = d.pop("query")

        taps = cast(list[str], d.pop("taps"))

        errors = cast(list[str], d.pop("errors", UNSET))

        hub_search_result = cls(
            hits=hits,
            query=query,
            taps=taps,
            errors=errors,
        )

        hub_search_result.additional_properties = d
        return hub_search_result

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
