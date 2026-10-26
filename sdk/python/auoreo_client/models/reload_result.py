from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.reload_result_changes import ReloadResultChanges


T = TypeVar("T", bound="ReloadResult")


@_attrs_define
class ReloadResult:
    """
    Attributes:
        changes (ReloadResultChanges):
        errors (list[str]):
        generation (int):
        kind (None | str | Unset): The `?kind=` scope (I5); None = everything.
        note (None | str | Unset):
    """

    changes: ReloadResultChanges
    errors: list[str]
    generation: int
    kind: None | str | Unset = UNSET
    note: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        changes = self.changes.to_dict()

        errors = self.errors

        generation = self.generation

        kind: None | str | Unset
        if isinstance(self.kind, Unset):
            kind = UNSET
        else:
            kind = self.kind

        note: None | str | Unset
        if isinstance(self.note, Unset):
            note = UNSET
        else:
            note = self.note

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "changes": changes,
                "errors": errors,
                "generation": generation,
            }
        )
        if kind is not UNSET:
            field_dict["kind"] = kind
        if note is not UNSET:
            field_dict["note"] = note

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.reload_result_changes import ReloadResultChanges  # noqa: PLC0415

        d = dict(src_dict)
        changes = ReloadResultChanges.from_dict(d.pop("changes"))

        errors = cast(list[str], d.pop("errors"))

        generation = d.pop("generation")

        def _parse_kind(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        kind = _parse_kind(d.pop("kind", UNSET))

        def _parse_note(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        note = _parse_note(d.pop("note", UNSET))

        reload_result = cls(
            changes=changes,
            errors=errors,
            generation=generation,
            kind=kind,
            note=note,
        )

        reload_result.additional_properties = d
        return reload_result

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
