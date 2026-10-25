from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.answer_responses_item import AnswerResponsesItem


T = TypeVar("T", bound="Answer")


@_attrs_define
class Answer:
    """
    Attributes:
        responses (list[AnswerResponsesItem]):
    """

    responses: list[AnswerResponsesItem]

    def to_dict(self) -> dict[str, Any]:
        responses = []
        for responses_item_data in self.responses:
            responses_item = responses_item_data.to_dict()
            responses.append(responses_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "responses": responses,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.answer_responses_item import AnswerResponsesItem  # noqa: PLC0415

        d = dict(src_dict)
        responses = []
        _responses = d.pop("responses")
        for responses_item_data in _responses:
            responses_item = AnswerResponsesItem.from_dict(responses_item_data)

            responses.append(responses_item)

        answer = cls(
            responses=responses,
        )

        return answer
