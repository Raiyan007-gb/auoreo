from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.provider_connection_out_status import ProviderConnectionOutStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="ProviderConnectionOut")


@_attrs_define
class ProviderConnectionOut:
    """
    Attributes:
        created_at (datetime.datetime):
        id (int):
        label (str):
        model_count (int):
        provider (str):
        provider_name (str):
        status (ProviderConnectionOutStatus):
        updated_at (datetime.datetime):
        api_version (None | str | Unset):
        base_url (None | str | Unset):
        checked_at (datetime.datetime | None | Unset):
        last_error (None | str | Unset): The provider's last error, redacted
        secret_name (None | str | Unset): Where the key lives (name only)
    """

    created_at: datetime.datetime
    id: int
    label: str
    model_count: int
    provider: str
    provider_name: str
    status: ProviderConnectionOutStatus
    updated_at: datetime.datetime
    api_version: None | str | Unset = UNSET
    base_url: None | str | Unset = UNSET
    checked_at: datetime.datetime | None | Unset = UNSET
    last_error: None | str | Unset = UNSET
    secret_name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = self.id

        label = self.label

        model_count = self.model_count

        provider = self.provider

        provider_name = self.provider_name

        status = self.status.value

        updated_at = self.updated_at.isoformat()

        api_version: None | str | Unset
        if isinstance(self.api_version, Unset):
            api_version = UNSET
        else:
            api_version = self.api_version

        base_url: None | str | Unset
        if isinstance(self.base_url, Unset):
            base_url = UNSET
        else:
            base_url = self.base_url

        checked_at: None | str | Unset
        if isinstance(self.checked_at, Unset):
            checked_at = UNSET
        elif isinstance(self.checked_at, datetime.datetime):
            checked_at = self.checked_at.isoformat()
        else:
            checked_at = self.checked_at

        last_error: None | str | Unset
        if isinstance(self.last_error, Unset):
            last_error = UNSET
        else:
            last_error = self.last_error

        secret_name: None | str | Unset
        if isinstance(self.secret_name, Unset):
            secret_name = UNSET
        else:
            secret_name = self.secret_name

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "label": label,
                "model_count": model_count,
                "provider": provider,
                "provider_name": provider_name,
                "status": status,
                "updated_at": updated_at,
            }
        )
        if api_version is not UNSET:
            field_dict["api_version"] = api_version
        if base_url is not UNSET:
            field_dict["base_url"] = base_url
        if checked_at is not UNSET:
            field_dict["checked_at"] = checked_at
        if last_error is not UNSET:
            field_dict["last_error"] = last_error
        if secret_name is not UNSET:
            field_dict["secret_name"] = secret_name

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        label = d.pop("label")

        model_count = d.pop("model_count")

        provider = d.pop("provider")

        provider_name = d.pop("provider_name")

        status = ProviderConnectionOutStatus(d.pop("status"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        def _parse_api_version(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        api_version = _parse_api_version(d.pop("api_version", UNSET))

        def _parse_base_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        base_url = _parse_base_url(d.pop("base_url", UNSET))

        def _parse_checked_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                checked_at_type_0 = datetime.datetime.fromisoformat(data)

                return checked_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        checked_at = _parse_checked_at(d.pop("checked_at", UNSET))

        def _parse_last_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        last_error = _parse_last_error(d.pop("last_error", UNSET))

        def _parse_secret_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        secret_name = _parse_secret_name(d.pop("secret_name", UNSET))

        provider_connection_out = cls(
            created_at=created_at,
            id=id,
            label=label,
            model_count=model_count,
            provider=provider,
            provider_name=provider_name,
            status=status,
            updated_at=updated_at,
            api_version=api_version,
            base_url=base_url,
            checked_at=checked_at,
            last_error=last_error,
            secret_name=secret_name,
        )

        provider_connection_out.additional_properties = d
        return provider_connection_out

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
