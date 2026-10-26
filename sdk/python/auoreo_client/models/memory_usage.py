from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="MemoryUsage")


@_attrs_define
class MemoryUsage:
    """Long-term memory size plus how much context compaction has saved (`compaction.completed`).

    Attributes:
        enabled (bool):
        archived_facts (int | Unset):  Default: 0.
        archived_messages (int | Unset):  Default: 0.
        chunks (int | Unset):  Default: 0.
        compactions (int | Unset):  Default: 0.
        db_bytes (int | Unset):  Default: 0.
        facts (int | Unset):  Default: 0.
        post_tokens (int | Unset):  Default: 0.
        pre_tokens (int | Unset):  Default: 0.
        saved_ratio (float | Unset):  Default: 0.0.
        transcript (int | Unset):  Default: 0.
        vector (bool | Unset):  Default: False.
    """

    enabled: bool
    archived_facts: int | Unset = 0
    archived_messages: int | Unset = 0
    chunks: int | Unset = 0
    compactions: int | Unset = 0
    db_bytes: int | Unset = 0
    facts: int | Unset = 0
    post_tokens: int | Unset = 0
    pre_tokens: int | Unset = 0
    saved_ratio: float | Unset = 0.0
    transcript: int | Unset = 0
    vector: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        enabled = self.enabled

        archived_facts = self.archived_facts

        archived_messages = self.archived_messages

        chunks = self.chunks

        compactions = self.compactions

        db_bytes = self.db_bytes

        facts = self.facts

        post_tokens = self.post_tokens

        pre_tokens = self.pre_tokens

        saved_ratio = self.saved_ratio

        transcript = self.transcript

        vector = self.vector

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "enabled": enabled,
            }
        )
        if archived_facts is not UNSET:
            field_dict["archived_facts"] = archived_facts
        if archived_messages is not UNSET:
            field_dict["archived_messages"] = archived_messages
        if chunks is not UNSET:
            field_dict["chunks"] = chunks
        if compactions is not UNSET:
            field_dict["compactions"] = compactions
        if db_bytes is not UNSET:
            field_dict["db_bytes"] = db_bytes
        if facts is not UNSET:
            field_dict["facts"] = facts
        if post_tokens is not UNSET:
            field_dict["post_tokens"] = post_tokens
        if pre_tokens is not UNSET:
            field_dict["pre_tokens"] = pre_tokens
        if saved_ratio is not UNSET:
            field_dict["saved_ratio"] = saved_ratio
        if transcript is not UNSET:
            field_dict["transcript"] = transcript
        if vector is not UNSET:
            field_dict["vector"] = vector

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        enabled = d.pop("enabled")

        archived_facts = d.pop("archived_facts", UNSET)

        archived_messages = d.pop("archived_messages", UNSET)

        chunks = d.pop("chunks", UNSET)

        compactions = d.pop("compactions", UNSET)

        db_bytes = d.pop("db_bytes", UNSET)

        facts = d.pop("facts", UNSET)

        post_tokens = d.pop("post_tokens", UNSET)

        pre_tokens = d.pop("pre_tokens", UNSET)

        saved_ratio = d.pop("saved_ratio", UNSET)

        transcript = d.pop("transcript", UNSET)

        vector = d.pop("vector", UNSET)

        memory_usage = cls(
            enabled=enabled,
            archived_facts=archived_facts,
            archived_messages=archived_messages,
            chunks=chunks,
            compactions=compactions,
            db_bytes=db_bytes,
            facts=facts,
            post_tokens=post_tokens,
            pre_tokens=pre_tokens,
            saved_ratio=saved_ratio,
            transcript=transcript,
            vector=vector,
        )

        memory_usage.additional_properties = d
        return memory_usage

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
