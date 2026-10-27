from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.provider_result_status import ProviderResultStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.provider_connection_out import ProviderConnectionOut


T = TypeVar("T", bound="ProviderResult")


@_attrs_define
class ProviderResult:
    """Answer of add / update / refresh: the connection, or the provider's error.

    Attributes:
        ok (bool):
        status (ProviderResultStatus):
        connection (None | ProviderConnectionOut | Unset):
        error (None | str | Unset):
        model_count (int | Unset):  Default: 0.
    """

    ok: bool
    status: ProviderResultStatus
    connection: None | ProviderConnectionOut | Unset = UNSET
    error: None | str | Unset = UNSET
    model_count: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.provider_connection_out import ProviderConnectionOut  # noqa: PLC0415

        ok = self.ok

        status = self.status.value

        connection: dict[str, Any] | None | Unset
        if isinstance(self.connection, Unset):
            connection = UNSET
        elif isinstance(self.connection, ProviderConnectionOut):
            connection = self.connection.to_dict()
        else:
            connection = self.connection

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        model_count = self.model_count

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "ok": ok,
                "status": status,
            }
        )
        if connection is not UNSET:
            field_dict["connection"] = connection
        if error is not UNSET:
            field_dict["error"] = error
        if model_count is not UNSET:
            field_dict["model_count"] = model_count

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.provider_connection_out import ProviderConnectionOut  # noqa: PLC0415

        d = dict(src_dict)
        ok = d.pop("ok")

        status = ProviderResultStatus(d.pop("status"))

        def _parse_connection(data: object) -> None | ProviderConnectionOut | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                connection_type_0 = ProviderConnectionOut.from_dict(data)

                return connection_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | ProviderConnectionOut | Unset, data)

        connection = _parse_connection(d.pop("connection", UNSET))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        model_count = d.pop("model_count", UNSET)

        provider_result = cls(
            ok=ok,
            status=status,
            connection=connection,
            error=error,
            model_count=model_count,
        )

        provider_result.additional_properties = d
        return provider_result

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
