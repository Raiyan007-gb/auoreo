from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.suggestion_info import SuggestionInfo


T = TypeVar("T", bound="SuggestionAccepted")


@_attrs_define
class SuggestionAccepted:
    """
    Attributes:
        suggestion (SuggestionInfo):
        run_id (None | str | Unset):
        session_id (None | str | Unset): Session the accepted prompt runs in
    """

    suggestion: SuggestionInfo
    run_id: None | str | Unset = UNSET
    session_id: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        suggestion = self.suggestion.to_dict()

        run_id: None | str | Unset
        if isinstance(self.run_id, Unset):
            run_id = UNSET
        else:
            run_id = self.run_id

        session_id: None | str | Unset
        if isinstance(self.session_id, Unset):
            session_id = UNSET
        else:
            session_id = self.session_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "suggestion": suggestion,
            }
        )
        if run_id is not UNSET:
            field_dict["run_id"] = run_id
        if session_id is not UNSET:
            field_dict["session_id"] = session_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.suggestion_info import SuggestionInfo  # noqa: PLC0415

        d = dict(src_dict)
        suggestion = SuggestionInfo.from_dict(d.pop("suggestion"))

        def _parse_run_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        run_id = _parse_run_id(d.pop("run_id", UNSET))

        def _parse_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        session_id = _parse_session_id(d.pop("session_id", UNSET))

        suggestion_accepted = cls(
            suggestion=suggestion,
            run_id=run_id,
            session_id=session_id,
        )

        suggestion_accepted.additional_properties = d
        return suggestion_accepted

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
