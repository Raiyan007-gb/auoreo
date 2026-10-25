from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.memory_fact import MemoryFact


T = TypeVar("T", bound="MemoryFactPage")


@_attrs_define
class MemoryFactPage:
    """
    Attributes:
        facts (list[MemoryFact]):
        total (int): Matches before limit/offset
    """

    facts: list[MemoryFact]
    total: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        facts = []
        for facts_item_data in self.facts:
            facts_item = facts_item_data.to_dict()
            facts.append(facts_item)

        total = self.total

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "facts": facts,
                "total": total,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.memory_fact import MemoryFact  # noqa: PLC0415

        d = dict(src_dict)
        facts = []
        _facts = d.pop("facts")
        for facts_item_data in _facts:
            facts_item = MemoryFact.from_dict(facts_item_data)

            facts.append(facts_item)

        total = d.pop("total")

        memory_fact_page = cls(
            facts=facts,
            total=total,
        )

        memory_fact_page.additional_properties = d
        return memory_fact_page

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
