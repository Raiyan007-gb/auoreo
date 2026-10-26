from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.secret_write_result_action import SecretWriteResultAction
from ..types import UNSET, Unset

T = TypeVar("T", bound="SecretWriteResult")


@_attrs_define
class SecretWriteResult:
    """
    Attributes:
        action (SecretWriteResultAction):
        name (str):
        reloaded (bool):
        kept_previous (bool | Unset):  Default: False.
        prev_expires_at (datetime.datetime | None | Unset):
    """

    action: SecretWriteResultAction
    name: str
    reloaded: bool
    kept_previous: bool | Unset = False
    prev_expires_at: datetime.datetime | None | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        action = self.action.value

        name = self.name

        reloaded = self.reloaded

        kept_previous = self.kept_previous

        prev_expires_at: None | str | Unset
        if isinstance(self.prev_expires_at, Unset):
            prev_expires_at = UNSET
        elif isinstance(self.prev_expires_at, datetime.datetime):
            prev_expires_at = self.prev_expires_at.isoformat()
        else:
            prev_expires_at = self.prev_expires_at

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "action": action,
                "name": name,
                "reloaded": reloaded,
            }
        )
        if kept_previous is not UNSET:
            field_dict["kept_previous"] = kept_previous
        if prev_expires_at is not UNSET:
            field_dict["prev_expires_at"] = prev_expires_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        action = SecretWriteResultAction(d.pop("action"))

        name = d.pop("name")

        reloaded = d.pop("reloaded")

        kept_previous = d.pop("kept_previous", UNSET)

        def _parse_prev_expires_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                prev_expires_at_type_0 = datetime.datetime.fromisoformat(data)

                return prev_expires_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        prev_expires_at = _parse_prev_expires_at(d.pop("prev_expires_at", UNSET))

        secret_write_result = cls(
            action=action,
            name=name,
            reloaded=reloaded,
            kept_previous=kept_previous,
            prev_expires_at=prev_expires_at,
        )

        secret_write_result.additional_properties = d
        return secret_write_result

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
