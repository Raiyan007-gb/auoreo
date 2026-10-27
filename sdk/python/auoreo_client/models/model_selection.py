from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.selected_model import SelectedModel


T = TypeVar("T", bound="ModelSelection")


@_attrs_define
class ModelSelection:
    """
    Attributes:
        embedding (SelectedModel):
        main (SelectedModel):
        small (SelectedModel):
    """

    embedding: SelectedModel
    main: SelectedModel
    small: SelectedModel
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        embedding = self.embedding.to_dict()

        main = self.main.to_dict()

        small = self.small.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "embedding": embedding,
                "main": main,
                "small": small,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.selected_model import SelectedModel  # noqa: PLC0415

        d = dict(src_dict)
        embedding = SelectedModel.from_dict(d.pop("embedding"))

        main = SelectedModel.from_dict(d.pop("main"))

        small = SelectedModel.from_dict(d.pop("small"))

        model_selection = cls(
            embedding=embedding,
            main=main,
            small=small,
        )

        model_selection.additional_properties = d
        return model_selection

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
