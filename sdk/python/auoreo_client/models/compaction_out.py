from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CompactionOut")


@_attrs_define
class CompactionOut:
    """
    Attributes:
        archived (int):
        compaction_id (str):
        post_tokens (int):
        pre_tokens (int):
        pruned (int):
        summary_chars (int):
        source (str | Unset):  Default: 'summary'.
    """

    archived: int
    compaction_id: str
    post_tokens: int
    pre_tokens: int
    pruned: int
    summary_chars: int
    source: str | Unset = "summary"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        archived = self.archived

        compaction_id = self.compaction_id

        post_tokens = self.post_tokens

        pre_tokens = self.pre_tokens

        pruned = self.pruned

        summary_chars = self.summary_chars

        source = self.source

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "archived": archived,
                "compaction_id": compaction_id,
                "post_tokens": post_tokens,
                "pre_tokens": pre_tokens,
                "pruned": pruned,
                "summary_chars": summary_chars,
            }
        )
        if source is not UNSET:
            field_dict["source"] = source

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        archived = d.pop("archived")

        compaction_id = d.pop("compaction_id")

        post_tokens = d.pop("post_tokens")

        pre_tokens = d.pop("pre_tokens")

        pruned = d.pop("pruned")

        summary_chars = d.pop("summary_chars")

        source = d.pop("source", UNSET)

        compaction_out = cls(
            archived=archived,
            compaction_id=compaction_id,
            post_tokens=post_tokens,
            pre_tokens=pre_tokens,
            pruned=pruned,
            summary_chars=summary_chars,
            source=source,
        )

        compaction_out.additional_properties = d
        return compaction_out

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
