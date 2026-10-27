from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.provider_models_status import ProviderModelsStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.provider_model_out import ProviderModelOut


T = TypeVar("T", bound="ProviderModels")


@_attrs_define
class ProviderModels:
    """
    Attributes:
        connection_id (int):
        models (list[ProviderModelOut]):
        status (ProviderModelsStatus):
        error (None | str | Unset):
        fetched_at (datetime.datetime | None | Unset):
        stale (bool | Unset): Older than the cache TTL (1 h) Default: False.
    """

    connection_id: int
    models: list[ProviderModelOut]
    status: ProviderModelsStatus
    error: None | str | Unset = UNSET
    fetched_at: datetime.datetime | None | Unset = UNSET
    stale: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        connection_id = self.connection_id

        models = []
        for models_item_data in self.models:
            models_item = models_item_data.to_dict()
            models.append(models_item)

        status = self.status.value

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        fetched_at: None | str | Unset
        if isinstance(self.fetched_at, Unset):
            fetched_at = UNSET
        elif isinstance(self.fetched_at, datetime.datetime):
            fetched_at = self.fetched_at.isoformat()
        else:
            fetched_at = self.fetched_at

        stale = self.stale

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "connection_id": connection_id,
                "models": models,
                "status": status,
            }
        )
        if error is not UNSET:
            field_dict["error"] = error
        if fetched_at is not UNSET:
            field_dict["fetched_at"] = fetched_at
        if stale is not UNSET:
            field_dict["stale"] = stale

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.provider_model_out import ProviderModelOut  # noqa: PLC0415

        d = dict(src_dict)
        connection_id = d.pop("connection_id")

        models = []
        _models = d.pop("models")
        for models_item_data in _models:
            models_item = ProviderModelOut.from_dict(models_item_data)

            models.append(models_item)

        status = ProviderModelsStatus(d.pop("status"))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        def _parse_fetched_at(data: object) -> datetime.datetime | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                fetched_at_type_0 = datetime.datetime.fromisoformat(data)

                return fetched_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None | Unset, data)

        fetched_at = _parse_fetched_at(d.pop("fetched_at", UNSET))

        stale = d.pop("stale", UNSET)

        provider_models = cls(
            connection_id=connection_id,
            models=models,
            status=status,
            error=error,
            fetched_at=fetched_at,
            stale=stale,
        )

        provider_models.additional_properties = d
        return provider_models

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
