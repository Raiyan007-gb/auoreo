from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.suggestion_create_action import SuggestionCreateAction


T = TypeVar("T", bound="SuggestionCreate")


@_attrs_define
class SuggestionCreate:
    """Propose a suggestion (integrations, tests). It is still only executed on accept.

    Attributes:
        title (str):
        action (SuggestionCreateAction | Unset): e.g. {"type": "prompt", "agent", "text"}
        body (str | Unset):  Default: ''.
        dedupe_key (None | str | Unset):
        deliver (str | Unset):  Default: 'origin'.
        kind (str | Unset):  Default: 'info'.
        origin_session (None | str | Unset):
        ttl_s (int | Unset):  Default: 86400.
    """

    title: str
    action: SuggestionCreateAction | Unset = UNSET
    body: str | Unset = ""
    dedupe_key: None | str | Unset = UNSET
    deliver: str | Unset = "origin"
    kind: str | Unset = "info"
    origin_session: None | str | Unset = UNSET
    ttl_s: int | Unset = 86400
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        title = self.title

        action: dict[str, Any] | Unset = UNSET
        if not isinstance(self.action, Unset):
            action = self.action.to_dict()

        body = self.body

        dedupe_key: None | str | Unset
        if isinstance(self.dedupe_key, Unset):
            dedupe_key = UNSET
        else:
            dedupe_key = self.dedupe_key

        deliver = self.deliver

        kind = self.kind

        origin_session: None | str | Unset
        if isinstance(self.origin_session, Unset):
            origin_session = UNSET
        else:
            origin_session = self.origin_session

        ttl_s = self.ttl_s

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "title": title,
            }
        )
        if action is not UNSET:
            field_dict["action"] = action
        if body is not UNSET:
            field_dict["body"] = body
        if dedupe_key is not UNSET:
            field_dict["dedupe_key"] = dedupe_key
        if deliver is not UNSET:
            field_dict["deliver"] = deliver
        if kind is not UNSET:
            field_dict["kind"] = kind
        if origin_session is not UNSET:
            field_dict["origin_session"] = origin_session
        if ttl_s is not UNSET:
            field_dict["ttl_s"] = ttl_s

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.suggestion_create_action import SuggestionCreateAction  # noqa: PLC0415

        d = dict(src_dict)
        title = d.pop("title")

        _action = d.pop("action", UNSET)
        action: SuggestionCreateAction | Unset
        if isinstance(_action, Unset):
            action = UNSET
        else:
            action = SuggestionCreateAction.from_dict(_action)

        body = d.pop("body", UNSET)

        def _parse_dedupe_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        dedupe_key = _parse_dedupe_key(d.pop("dedupe_key", UNSET))

        deliver = d.pop("deliver", UNSET)

        kind = d.pop("kind", UNSET)

        def _parse_origin_session(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        origin_session = _parse_origin_session(d.pop("origin_session", UNSET))

        ttl_s = d.pop("ttl_s", UNSET)

        suggestion_create = cls(
            title=title,
            action=action,
            body=body,
            dedupe_key=dedupe_key,
            deliver=deliver,
            kind=kind,
            origin_session=origin_session,
            ttl_s=ttl_s,
        )

        suggestion_create.additional_properties = d
        return suggestion_create

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
