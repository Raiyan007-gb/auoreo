from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="Health")


@_attrs_define
class Health:
    """
    Attributes:
        gateway (str):
        generation (int):
        status (Literal['ok']):
        version (str):
        code_sha (None | str | Unset):
    """

    gateway: str
    generation: int
    status: Literal["ok"]
    version: str
    code_sha: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        gateway = self.gateway

        generation = self.generation

        status = self.status

        version = self.version

        code_sha: None | str | Unset
        if isinstance(self.code_sha, Unset):
            code_sha = UNSET
        else:
            code_sha = self.code_sha

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "gateway": gateway,
                "generation": generation,
                "status": status,
                "version": version,
            }
        )
        if code_sha is not UNSET:
            field_dict["code_sha"] = code_sha

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        gateway = d.pop("gateway")

        generation = d.pop("generation")

        status = cast(Literal["ok"], d.pop("status"))
        if status != "ok":
            raise ValueError(f"status must match const 'ok', got '{status}'")

        version = d.pop("version")

        def _parse_code_sha(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        code_sha = _parse_code_sha(d.pop("code_sha", UNSET))

        health = cls(
            gateway=gateway,
            generation=generation,
            status=status,
            version=version,
            code_sha=code_sha,
        )

        health.additional_properties = d
        return health

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
