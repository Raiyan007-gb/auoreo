from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="BundledMcpResult")


@_attrs_define
class BundledMcpResult:
    """`POST /api/harness/mcp/bundled/{name}`: the runtime definition written, then reloaded.

    Attributes:
        errors (list[str]):
        generation (int):
        name (str):
        path (str): The `<home>/mcp/<name>.yaml` that now enables the server.
    """

    errors: list[str]
    generation: int
    name: str
    path: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        errors = self.errors

        generation = self.generation

        name = self.name

        path = self.path

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "errors": errors,
                "generation": generation,
                "name": name,
                "path": path,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        errors = cast(list[str], d.pop("errors"))

        generation = d.pop("generation")

        name = d.pop("name")

        path = d.pop("path")

        bundled_mcp_result = cls(
            errors=errors,
            generation=generation,
            name=name,
            path=path,
        )

        bundled_mcp_result.additional_properties = d
        return bundled_mcp_result

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
