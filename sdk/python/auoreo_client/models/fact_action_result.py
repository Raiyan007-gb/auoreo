from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="FactActionResult")


@_attrs_define
class FactActionResult:
    """
    Attributes:
        id (int):
        ok (bool):
        trust (float | None | Unset):
    """

    id: int
    ok: bool
    trust: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        ok = self.ok

        trust: float | None | Unset
        if isinstance(self.trust, Unset):
            trust = UNSET
        else:
            trust = self.trust

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
                "ok": ok,
            }
        )
        if trust is not UNSET:
            field_dict["trust"] = trust

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        ok = d.pop("ok")

        def _parse_trust(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        trust = _parse_trust(d.pop("trust", UNSET))

        fact_action_result = cls(
            id=id,
            ok=ok,
            trust=trust,
        )

        fact_action_result.additional_properties = d
        return fact_action_result

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
