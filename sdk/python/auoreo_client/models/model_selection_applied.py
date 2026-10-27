from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.model_selection import ModelSelection


T = TypeVar("T", bound="ModelSelectionApplied")


@_attrs_define
class ModelSelectionApplied:
    """
    Attributes:
        changed (list[str]):
        live (list[str]):
        note (str):
        restart_required (list[str]):
        selection (ModelSelection):
    """

    changed: list[str]
    live: list[str]
    note: str
    restart_required: list[str]
    selection: ModelSelection
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        changed = self.changed

        live = self.live

        note = self.note

        restart_required = self.restart_required

        selection = self.selection.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "changed": changed,
                "live": live,
                "note": note,
                "restart_required": restart_required,
                "selection": selection,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_selection import ModelSelection  # noqa: PLC0415

        d = dict(src_dict)
        changed = cast(list[str], d.pop("changed"))

        live = cast(list[str], d.pop("live"))

        note = d.pop("note")

        restart_required = cast(list[str], d.pop("restart_required"))

        selection = ModelSelection.from_dict(d.pop("selection"))

        model_selection_applied = cls(
            changed=changed,
            live=live,
            note=note,
            restart_required=restart_required,
            selection=selection,
        )

        model_selection_applied.additional_properties = d
        return model_selection_applied

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
