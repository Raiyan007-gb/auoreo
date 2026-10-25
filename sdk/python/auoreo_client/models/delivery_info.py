from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.delivery_info_result import DeliveryInfoResult


T = TypeVar("T", bound="DeliveryInfo")


@_attrs_define
class DeliveryInfo:
    """
    Attributes:
        attempts (int):
        claimed_at (datetime.datetime | None):
        created_at (datetime.datetime):
        error (None | str):
        external_id (str):
        id (int):
        kind (str):
        result (DeliveryInfoResult):
        session_id (None | str):
        source (str):
        status (str): pending | delivering | delivered | failed | unknown
        target (str):
        updated_at (datetime.datetime):
    """

    attempts: int
    claimed_at: datetime.datetime | None
    created_at: datetime.datetime
    error: None | str
    external_id: str
    id: int
    kind: str
    result: DeliveryInfoResult
    session_id: None | str
    source: str
    status: str
    target: str
    updated_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        attempts = self.attempts

        claimed_at: None | str
        if isinstance(self.claimed_at, datetime.datetime):
            claimed_at = self.claimed_at.isoformat()
        else:
            claimed_at = self.claimed_at

        created_at = self.created_at.isoformat()

        error: None | str
        error = self.error

        external_id = self.external_id

        id = self.id

        kind = self.kind

        result = self.result.to_dict()

        session_id: None | str
        session_id = self.session_id

        source = self.source

        status = self.status

        target = self.target

        updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "attempts": attempts,
                "claimed_at": claimed_at,
                "created_at": created_at,
                "error": error,
                "external_id": external_id,
                "id": id,
                "kind": kind,
                "result": result,
                "session_id": session_id,
                "source": source,
                "status": status,
                "target": target,
                "updated_at": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.delivery_info_result import DeliveryInfoResult  # noqa: PLC0415

        d = dict(src_dict)
        attempts = d.pop("attempts")

        def _parse_claimed_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                claimed_at_type_0 = datetime.datetime.fromisoformat(data)

                return claimed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        claimed_at = _parse_claimed_at(d.pop("claimed_at"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        error = _parse_error(d.pop("error"))

        external_id = d.pop("external_id")

        id = d.pop("id")

        kind = d.pop("kind")

        result = DeliveryInfoResult.from_dict(d.pop("result"))

        def _parse_session_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        session_id = _parse_session_id(d.pop("session_id"))

        source = d.pop("source")

        status = d.pop("status")

        target = d.pop("target")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        delivery_info = cls(
            attempts=attempts,
            claimed_at=claimed_at,
            created_at=created_at,
            error=error,
            external_id=external_id,
            id=id,
            kind=kind,
            result=result,
            session_id=session_id,
            source=source,
            status=status,
            target=target,
            updated_at=updated_at,
        )

        delivery_info.additional_properties = d
        return delivery_info

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
