from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.open_ai_choice import OpenAIChoice
    from ..models.open_ai_usage import OpenAIUsage


T = TypeVar("T", bound="OpenAIChatCompletion")


@_attrs_define
class OpenAIChatCompletion:
    """
    Attributes:
        choices (list[OpenAIChoice]):
        created (int):
        id (str):
        model (str):
        usage (OpenAIUsage):
        object_ (Literal['chat.completion'] | Unset):  Default: 'chat.completion'.
    """

    choices: list[OpenAIChoice]
    created: int
    id: str
    model: str
    usage: OpenAIUsage
    object_: Literal["chat.completion"] | Unset = "chat.completion"
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        choices = []
        for choices_item_data in self.choices:
            choices_item = choices_item_data.to_dict()
            choices.append(choices_item)

        created = self.created

        id = self.id

        model = self.model

        usage = self.usage.to_dict()

        object_ = self.object_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "choices": choices,
                "created": created,
                "id": id,
                "model": model,
                "usage": usage,
            }
        )
        if object_ is not UNSET:
            field_dict["object"] = object_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.open_ai_choice import OpenAIChoice  # noqa: PLC0415
        from ..models.open_ai_usage import OpenAIUsage  # noqa: PLC0415

        d = dict(src_dict)
        choices = []
        _choices = d.pop("choices")
        for choices_item_data in _choices:
            choices_item = OpenAIChoice.from_dict(choices_item_data)

            choices.append(choices_item)

        created = d.pop("created")

        id = d.pop("id")

        model = d.pop("model")

        usage = OpenAIUsage.from_dict(d.pop("usage"))

        object_ = cast(Literal["chat.completion"] | Unset, d.pop("object", UNSET))
        if object_ != "chat.completion" and not isinstance(object_, Unset):
            raise ValueError(f"object must match const 'chat.completion', got '{object_}'")

        open_ai_chat_completion = cls(
            choices=choices,
            created=created,
            id=id,
            model=model,
            usage=usage,
            object_=object_,
        )

        open_ai_chat_completion.additional_properties = d
        return open_ai_chat_completion

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
