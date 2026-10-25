from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.suggestion_info_status import SuggestionInfoStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.suggestion_info_action import SuggestionInfoAction


T = TypeVar("T", bound="SuggestionInfo")


@_attrs_define
class SuggestionInfo:
    """
    Attributes:
        created_at (datetime.datetime):
        deliver (str): origin | home | all | <provider>[:<target>]
        id (int):
        kind (str):
        status (SuggestionInfoStatus):
        title (str):
        updated_at (datetime.datetime):
        action (SuggestionInfoAction | Unset):
        body (str | Unset):  Default: ''.
        dedupe_key (None | str | Unset):
        delivery_id (int | None | Unset):
        expires_at (datetime.datetime | None | Unset):
        origin_session (None | str | Unset):
    """

    created_at: datetime.datetime
    deliver: str
    id: int
    kind: str
    status: SuggestionInfoStatus
    title: str
    updated_at: datetime.datetime
    action: SuggestionInfoAction | Unset = UNSET
    body: str | Unset = ""
    dedupe_key: None | str | Unset = UNSET
    delivery_id: int | None | Unset = UNSET
    expires_at: datetime.datetime | None | Unset = UNSET
    origin_session: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        deliver = self.deliver

        id = self.id

        kind = self.kind

        status = self.status.value

        title = self.title

        updated_at = self.updated_at.isoformat()

        action: dict[str, Any] | Unset = UNSET
        if not isinstance(self.action, Unset):
            action = self.action.to_dict()

        body = self.body

        dedupe_key: None | str | Unset
        if isinstance(self.dedupe_key, Unset):
            dedupe_key = UNSET
        else:
            dedupe_key = self.dedupe_key

        delivery_id: int | None | Unset
        if isinstance(self.delivery_id, Unset):
            delivery_id = UNSET
        else:
            delivery_id = self.delivery_id

        expires_at: None | str | Unset
        if isinstance(self.expires_at, Unset):
            expires_at = UNSET
        elif isinstance(self.expires_at, datetime.datetime):
            expires_at = self.expires_at.isoformat()
        else:
            expires_at = self.expires_at

        origin_session: None | str | Unset
        if isinstance(self.origin_session, Unset):
            origin_session = UNSET
        else:
            origin_session = self.origin_session

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "deliver": deliver,
                "id": id,
                "kind": kind,
                "status": status,
                "title": title,
                "updated_at": updated_at,
            }
        )
        if action is not UNSET:
            field_dict["action"] = action
        if body is not UNSET:
            field_dict["body"] = body
        if dedupe_key is not UNSET:
            field_dict["dedupe_key"] = dedupe_key
        if delivery_id is not UNSET:
            field_dict["delivery_id"] = delivery_id
        if expires_at is not UNSET:
            field_dict["expires_at"] = expires_at
        if origin_session is not UNSET:
            field_dict["origin_session"] = origin_session

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.suggestion_info_action import SuggestionInfoAction  # noqa: PLC0415

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        deliver = d.pop("deliver")

        id = d.pop("id")

        kind = d.pop("kind")

        status = SuggestionInfoStatus(d.pop("status"))

        title = d.pop("title")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        _action = d.pop("action", UNSET)
        action: SuggestionInfoAction | Unset
        if isinstance(_action, Unset):
            action = UNSET
        else:
            action = SuggestionInfoAction.from_dict(_action)

        body = d.pop("body", UNSET)

        def _parse_dedupe_key(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        dedupe_key = _parse_dedupe_key(d.pop("dedupe_key", UNSET))

        def _parse_delivery_id(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        delivery_id = _parse_delivery_id(d.pop("delivery_id", UNSET))

        def _parse_expires_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                expires_at_type_0 = datetime.datetime.fromisoformat(data)

                return expires_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        expires_at = _parse_expires_at(d.pop("expires_at", UNSET))

        def _parse_origin_session(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        origin_session = _parse_origin_session(d.pop("origin_session", UNSET))

        suggestion_info = cls(
            created_at=created_at,
            deliver=deliver,
            id=id,
            kind=kind,
            status=status,
            title=title,
            updated_at=updated_at,
            action=action,
            body=body,
            dedupe_key=dedupe_key,
            delivery_id=delivery_id,
            expires_at=expires_at,
            origin_session=origin_session,
        )

        suggestion_info.additional_properties = d
        return suggestion_info

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
