from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="MemoryFact")


@_attrs_define
class MemoryFact:
    """
    Attributes:
        category (str):
        content (str):
        created_at (float):
        id (int):
        trust (float):
        updated_at (float):
        archived (bool | Unset):  Default: False.
        archived_at (float | None | Unset):
        last_used_at (float | None | Unset):
        pinned (bool | Unset):  Default: False.
        source (None | str | Unset):
        supersedes_id (int | None | Unset):
        use_count (int | Unset):  Default: 0.
        valid_from (float | None | Unset):
        valid_to (float | None | Unset):
    """

    category: str
    content: str
    created_at: float
    id: int
    trust: float
    updated_at: float
    archived: bool | Unset = False
    archived_at: float | None | Unset = UNSET
    last_used_at: float | None | Unset = UNSET
    pinned: bool | Unset = False
    source: None | str | Unset = UNSET
    supersedes_id: int | None | Unset = UNSET
    use_count: int | Unset = 0
    valid_from: float | None | Unset = UNSET
    valid_to: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        category = self.category

        content = self.content

        created_at = self.created_at

        id = self.id

        trust = self.trust

        updated_at = self.updated_at

        archived = self.archived

        archived_at: float | None | Unset
        if isinstance(self.archived_at, Unset):
            archived_at = UNSET
        else:
            archived_at = self.archived_at

        last_used_at: float | None | Unset
        if isinstance(self.last_used_at, Unset):
            last_used_at = UNSET
        else:
            last_used_at = self.last_used_at

        pinned = self.pinned

        source: None | str | Unset
        if isinstance(self.source, Unset):
            source = UNSET
        else:
            source = self.source

        supersedes_id: int | None | Unset
        if isinstance(self.supersedes_id, Unset):
            supersedes_id = UNSET
        else:
            supersedes_id = self.supersedes_id

        use_count = self.use_count

        valid_from: float | None | Unset
        if isinstance(self.valid_from, Unset):
            valid_from = UNSET
        else:
            valid_from = self.valid_from

        valid_to: float | None | Unset
        if isinstance(self.valid_to, Unset):
            valid_to = UNSET
        else:
            valid_to = self.valid_to

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "category": category,
                "content": content,
                "created_at": created_at,
                "id": id,
                "trust": trust,
                "updated_at": updated_at,
            }
        )
        if archived is not UNSET:
            field_dict["archived"] = archived
        if archived_at is not UNSET:
            field_dict["archived_at"] = archived_at
        if last_used_at is not UNSET:
            field_dict["last_used_at"] = last_used_at
        if pinned is not UNSET:
            field_dict["pinned"] = pinned
        if source is not UNSET:
            field_dict["source"] = source
        if supersedes_id is not UNSET:
            field_dict["supersedes_id"] = supersedes_id
        if use_count is not UNSET:
            field_dict["use_count"] = use_count
        if valid_from is not UNSET:
            field_dict["valid_from"] = valid_from
        if valid_to is not UNSET:
            field_dict["valid_to"] = valid_to

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        category = d.pop("category")

        content = d.pop("content")

        created_at = d.pop("created_at")

        id = d.pop("id")

        trust = d.pop("trust")

        updated_at = d.pop("updated_at")

        archived = d.pop("archived", UNSET)

        def _parse_archived_at(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        archived_at = _parse_archived_at(d.pop("archived_at", UNSET))

        def _parse_last_used_at(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        last_used_at = _parse_last_used_at(d.pop("last_used_at", UNSET))

        pinned = d.pop("pinned", UNSET)

        def _parse_source(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        source = _parse_source(d.pop("source", UNSET))

        def _parse_supersedes_id(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        supersedes_id = _parse_supersedes_id(d.pop("supersedes_id", UNSET))

        use_count = d.pop("use_count", UNSET)

        def _parse_valid_from(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        valid_from = _parse_valid_from(d.pop("valid_from", UNSET))

        def _parse_valid_to(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        valid_to = _parse_valid_to(d.pop("valid_to", UNSET))

        memory_fact = cls(
            category=category,
            content=content,
            created_at=created_at,
            id=id,
            trust=trust,
            updated_at=updated_at,
            archived=archived,
            archived_at=archived_at,
            last_used_at=last_used_at,
            pinned=pinned,
            source=source,
            supersedes_id=supersedes_id,
            use_count=use_count,
            valid_from=valid_from,
            valid_to=valid_to,
        )

        memory_fact.additional_properties = d
        return memory_fact

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
