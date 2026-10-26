from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.pending_question_out_questions_item import PendingQuestionOutQuestionsItem


T = TypeVar("T", bound="PendingQuestionOut")


@_attrs_define
class PendingQuestionOut:
    """
    Attributes:
        questions (list[PendingQuestionOutQuestionsItem]):
        request_id (str):
        session_id (str):
        expires_at (float | None | Unset):
        timeout_s (float | None | Unset):
    """

    questions: list[PendingQuestionOutQuestionsItem]
    request_id: str
    session_id: str
    expires_at: float | None | Unset = UNSET
    timeout_s: float | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        questions = []
        for questions_item_data in self.questions:
            questions_item = questions_item_data.to_dict()
            questions.append(questions_item)

        request_id = self.request_id

        session_id = self.session_id

        expires_at: float | None | Unset
        if isinstance(self.expires_at, Unset):
            expires_at = UNSET
        else:
            expires_at = self.expires_at

        timeout_s: float | None | Unset
        if isinstance(self.timeout_s, Unset):
            timeout_s = UNSET
        else:
            timeout_s = self.timeout_s

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "questions": questions,
                "request_id": request_id,
                "session_id": session_id,
            }
        )
        if expires_at is not UNSET:
            field_dict["expires_at"] = expires_at
        if timeout_s is not UNSET:
            field_dict["timeout_s"] = timeout_s

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.pending_question_out_questions_item import PendingQuestionOutQuestionsItem  # noqa: PLC0415

        d = dict(src_dict)
        questions = []
        _questions = d.pop("questions")
        for questions_item_data in _questions:
            questions_item = PendingQuestionOutQuestionsItem.from_dict(questions_item_data)

            questions.append(questions_item)

        request_id = d.pop("request_id")

        session_id = d.pop("session_id")

        def _parse_expires_at(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        expires_at = _parse_expires_at(d.pop("expires_at", UNSET))

        def _parse_timeout_s(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        timeout_s = _parse_timeout_s(d.pop("timeout_s", UNSET))

        pending_question_out = cls(
            questions=questions,
            request_id=request_id,
            session_id=session_id,
            expires_at=expires_at,
            timeout_s=timeout_s,
        )

        pending_question_out.additional_properties = d
        return pending_question_out

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
