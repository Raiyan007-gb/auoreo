from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.prefs_proposal_in_action import PrefsProposalInAction
from ..types import UNSET, Unset

T = TypeVar("T", bound="PrefsProposalIn")


@_attrs_define
class PrefsProposalIn:
    """
    Attributes:
        action (PrefsProposalInAction | Unset):  Default: PrefsProposalInAction.ADD.
        content (str | Unset):  Default: ''.
        old_text (None | str | Unset):
        reason (str | Unset):  Default: ''.
    """

    action: PrefsProposalInAction | Unset = PrefsProposalInAction.ADD
    content: str | Unset = ""
    old_text: None | str | Unset = UNSET
    reason: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        action: str | Unset = UNSET
        if not isinstance(self.action, Unset):
            action = self.action.value

        content = self.content

        old_text: None | str | Unset
        if isinstance(self.old_text, Unset):
            old_text = UNSET
        else:
            old_text = self.old_text

        reason = self.reason

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if action is not UNSET:
            field_dict["action"] = action
        if content is not UNSET:
            field_dict["content"] = content
        if old_text is not UNSET:
            field_dict["old_text"] = old_text
        if reason is not UNSET:
            field_dict["reason"] = reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        _action = d.pop("action", UNSET)
        action: PrefsProposalInAction | Unset
        if isinstance(_action, Unset):
            action = UNSET
        else:
            action = PrefsProposalInAction(_action)

        content = d.pop("content", UNSET)

        def _parse_old_text(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        old_text = _parse_old_text(d.pop("old_text", UNSET))

        reason = d.pop("reason", UNSET)

        prefs_proposal_in = cls(
            action=action,
            content=content,
            old_text=old_text,
            reason=reason,
        )

        prefs_proposal_in.additional_properties = d
        return prefs_proposal_in

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
