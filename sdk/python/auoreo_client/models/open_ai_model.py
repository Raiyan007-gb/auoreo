from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="OpenAIModel")


@_attrs_define
class OpenAIModel:
    """
    Attributes:
        id (str):
        created (int | Unset):  Default: 0.
        object_ (Literal['model'] | Unset):  Default: 'model'.
        owned_by (str | Unset):  Default: 'auoreo'.
    """

    id: str
    created: int | Unset = 0
    object_: Literal["model"] | Unset = "model"
    owned_by: str | Unset = "auoreo"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        id = self.id

        created = self.created

        object_ = self.object_

        owned_by = self.owned_by

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "id": id,
            }
        )
        if created is not UNSET:
            field_dict["created"] = created
        if object_ is not UNSET:
            field_dict["object"] = object_
        if owned_by is not UNSET:
            field_dict["owned_by"] = owned_by

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        id = d.pop("id")

        created = d.pop("created", UNSET)

        object_ = cast(Literal["model"] | Unset, d.pop("object", UNSET))
        if object_ != "model" and not isinstance(object_, Unset):
            raise ValueError(f"object must match const 'model', got '{object_}'")

        owned_by = d.pop("owned_by", UNSET)

        open_ai_model = cls(
            id=id,
            created=created,
            object_=object_,
            owned_by=owned_by,
        )

        open_ai_model.additional_properties = d
        return open_ai_model

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
