from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.env_entry_kind import EnvEntryKind
from ..models.env_entry_source import EnvEntrySource
from ..models.env_entry_tier import EnvEntryTier
from ..models.env_entry_type import EnvEntryType
from ..types import UNSET, Unset

T = TypeVar("T", bound="EnvEntry")


@_attrs_define
class EnvEntry:
    """
    Attributes:
        description (str):
        is_set (bool): A non-default value is in effect (secrets: a value exists)
        kind (EnvEntryKind):
        live (bool): A change applies without a restart
        name (str): The environment variable, e.g. `AUOREO_MODEL`
        source (EnvEntrySource):
        tier (EnvEntryTier):
        type_ (EnvEntryType):
        writable (bool):
        choices (list[str] | Unset):
        default (Any | Unset):
        field (None | str | Unset): Settings key (state.db) for `kind=setting`
        locked_reason (None | str | Unset): Why the web cannot change it
        nullable (bool | Unset):  Default: False.
        pending_restart (bool | Unset): The stored value differs from the running one Default: False.
        restart_reason (None | str | Unset):
        updated_at (datetime.datetime | None | Unset): Secrets: last write
        value (Any | Unset): Effective value (redacted); always null for secrets
    """

    description: str
    is_set: bool
    kind: EnvEntryKind
    live: bool
    name: str
    source: EnvEntrySource
    tier: EnvEntryTier
    type_: EnvEntryType
    writable: bool
    choices: list[str] | Unset = UNSET
    default: Any | Unset = UNSET
    field: None | str | Unset = UNSET
    locked_reason: None | str | Unset = UNSET
    nullable: bool | Unset = False
    pending_restart: bool | Unset = False
    restart_reason: None | str | Unset = UNSET
    updated_at: datetime.datetime | None | Unset = UNSET
    value: Any | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        is_set = self.is_set

        kind = self.kind.value

        live = self.live

        name = self.name

        source = self.source.value

        tier = self.tier.value

        type_ = self.type_.value

        writable = self.writable

        choices: list[str] | Unset = UNSET
        if not isinstance(self.choices, Unset):
            choices = self.choices

        default = self.default

        field: None | str | Unset
        if isinstance(self.field, Unset):
            field = UNSET
        else:
            field = self.field

        locked_reason: None | str | Unset
        if isinstance(self.locked_reason, Unset):
            locked_reason = UNSET
        else:
            locked_reason = self.locked_reason

        nullable = self.nullable

        pending_restart = self.pending_restart

        restart_reason: None | str | Unset
        if isinstance(self.restart_reason, Unset):
            restart_reason = UNSET
        else:
            restart_reason = self.restart_reason

        updated_at: None | str | Unset
        if isinstance(self.updated_at, Unset):
            updated_at = UNSET
        elif isinstance(self.updated_at, datetime.datetime):
            updated_at = self.updated_at.isoformat()
        else:
            updated_at = self.updated_at

        value = self.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "is_set": is_set,
                "kind": kind,
                "live": live,
                "name": name,
                "source": source,
                "tier": tier,
                "type": type_,
                "writable": writable,
            }
        )
        if choices is not UNSET:
            field_dict["choices"] = choices
        if default is not UNSET:
            field_dict["default"] = default
        if field is not UNSET:
            field_dict["field"] = field
        if locked_reason is not UNSET:
            field_dict["locked_reason"] = locked_reason
        if nullable is not UNSET:
            field_dict["nullable"] = nullable
        if pending_restart is not UNSET:
            field_dict["pending_restart"] = pending_restart
        if restart_reason is not UNSET:
            field_dict["restart_reason"] = restart_reason
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at
        if value is not UNSET:
            field_dict["value"] = value

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        description = d.pop("description")

        is_set = d.pop("is_set")

        kind = EnvEntryKind(d.pop("kind"))

        live = d.pop("live")

        name = d.pop("name")

        source = EnvEntrySource(d.pop("source"))

        tier = EnvEntryTier(d.pop("tier"))

        type_ = EnvEntryType(d.pop("type"))

        writable = d.pop("writable")

        choices = cast(list[str], d.pop("choices", UNSET))

        default = d.pop("default", UNSET)

        def _parse_field(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        field = _parse_field(d.pop("field", UNSET))

        def _parse_locked_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        locked_reason = _parse_locked_reason(d.pop("locked_reason", UNSET))

        nullable = d.pop("nullable", UNSET)

        pending_restart = d.pop("pending_restart", UNSET)

        def _parse_restart_reason(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        restart_reason = _parse_restart_reason(d.pop("restart_reason", UNSET))

        def _parse_updated_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                updated_at_type_0 = datetime.datetime.fromisoformat(data)

                return updated_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        updated_at = _parse_updated_at(d.pop("updated_at", UNSET))

        value = d.pop("value", UNSET)

        env_entry = cls(
            description=description,
            is_set=is_set,
            kind=kind,
            live=live,
            name=name,
            source=source,
            tier=tier,
            type_=type_,
            writable=writable,
            choices=choices,
            default=default,
            field=field,
            locked_reason=locked_reason,
            nullable=nullable,
            pending_restart=pending_restart,
            restart_reason=restart_reason,
            updated_at=updated_at,
            value=value,
        )

        env_entry.additional_properties = d
        return env_entry

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
