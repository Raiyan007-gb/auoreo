from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="BoardArtifact")


@_attrs_define
class BoardArtifact:
    """
    Attributes:
        bytes_ (int): UTF-8 size of the value (≤ 65536)
        created_at (datetime.datetime):
        key (str):
        producer (str): Member name (or `api:<actor>`) that wrote the current version
        team_id (str):
        updated_at (datetime.datetime):
        value (str):
        version (int): Starts at 1; every put bumps it
    """

    bytes_: int
    created_at: datetime.datetime
    key: str
    producer: str
    team_id: str
    updated_at: datetime.datetime
    value: str
    version: int
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        bytes_ = self.bytes_

        created_at = self.created_at.isoformat()

        key = self.key

        producer = self.producer

        team_id = self.team_id

        updated_at = self.updated_at.isoformat()

        value = self.value

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "bytes": bytes_,
                "created_at": created_at,
                "key": key,
                "producer": producer,
                "team_id": team_id,
                "updated_at": updated_at,
                "value": value,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        bytes_ = d.pop("bytes")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        key = d.pop("key")

        producer = d.pop("producer")

        team_id = d.pop("team_id")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        value = d.pop("value")

        version = d.pop("version")

        board_artifact = cls(
            bytes_=bytes_,
            created_at=created_at,
            key=key,
            producer=producer,
            team_id=team_id,
            updated_at=updated_at,
            value=value,
            version=version,
        )

        board_artifact.additional_properties = d
        return board_artifact

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
