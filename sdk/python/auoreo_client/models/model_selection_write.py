from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_ref import ModelRef


T = TypeVar("T", bound="ModelSelectionWrite")


@_attrs_define
class ModelSelectionWrite:
    """
    Attributes:
        embedding (ModelRef | None | Unset):
        main (ModelRef | None | Unset):
        small (ModelRef | None | Unset):
    """

    embedding: ModelRef | None | Unset = UNSET
    main: ModelRef | None | Unset = UNSET
    small: ModelRef | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.model_ref import ModelRef  # noqa: PLC0415

        embedding: dict[str, Any] | None | Unset
        if isinstance(self.embedding, Unset):
            embedding = UNSET
        elif isinstance(self.embedding, ModelRef):
            embedding = self.embedding.to_dict()
        else:
            embedding = self.embedding

        main: dict[str, Any] | None | Unset
        if isinstance(self.main, Unset):
            main = UNSET
        elif isinstance(self.main, ModelRef):
            main = self.main.to_dict()
        else:
            main = self.main

        small: dict[str, Any] | None | Unset
        if isinstance(self.small, Unset):
            small = UNSET
        elif isinstance(self.small, ModelRef):
            small = self.small.to_dict()
        else:
            small = self.small

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if embedding is not UNSET:
            field_dict["embedding"] = embedding
        if main is not UNSET:
            field_dict["main"] = main
        if small is not UNSET:
            field_dict["small"] = small

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_ref import ModelRef  # noqa: PLC0415

        d = dict(src_dict)

        def _parse_embedding(data: object) -> ModelRef | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                embedding_type_0 = ModelRef.from_dict(data)

                return embedding_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ModelRef | None | Unset, data)

        embedding = _parse_embedding(d.pop("embedding", UNSET))

        def _parse_main(data: object) -> ModelRef | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                main_type_0 = ModelRef.from_dict(data)

                return main_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ModelRef | None | Unset, data)

        main = _parse_main(d.pop("main", UNSET))

        def _parse_small(data: object) -> ModelRef | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                small_type_0 = ModelRef.from_dict(data)

                return small_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ModelRef | None | Unset, data)

        small = _parse_small(d.pop("small", UNSET))

        model_selection_write = cls(
            embedding=embedding,
            main=main,
            small=small,
        )

        model_selection_write.additional_properties = d
        return model_selection_write

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
