from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="UsageRecord")


@_attrs_define
class UsageRecord:
    """
    Attributes:
        cached_tokens (int):
        completion_tokens (int):
        cost_usd (float | None):
        created_at (datetime.datetime):
        estimated (bool):
        id (int):
        model (str):
        prompt_tokens (int):
        run_id (str):
        session_id (str):
    """

    cached_tokens: int
    completion_tokens: int
    cost_usd: float | None
    created_at: datetime.datetime
    estimated: bool
    id: int
    model: str
    prompt_tokens: int
    run_id: str
    session_id: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        cached_tokens = self.cached_tokens

        completion_tokens = self.completion_tokens

        cost_usd: float | None
        cost_usd = self.cost_usd

        created_at = self.created_at.isoformat()

        estimated = self.estimated

        id = self.id

        model = self.model

        prompt_tokens = self.prompt_tokens

        run_id = self.run_id

        session_id = self.session_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "cached_tokens": cached_tokens,
                "completion_tokens": completion_tokens,
                "cost_usd": cost_usd,
                "created_at": created_at,
                "estimated": estimated,
                "id": id,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "run_id": run_id,
                "session_id": session_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        cached_tokens = d.pop("cached_tokens")

        completion_tokens = d.pop("completion_tokens")

        def _parse_cost_usd(data: object) -> float | None:
            if data is None:
                return data
            return cast(float | None, data)

        cost_usd = _parse_cost_usd(d.pop("cost_usd"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        estimated = d.pop("estimated")

        id = d.pop("id")

        model = d.pop("model")

        prompt_tokens = d.pop("prompt_tokens")

        run_id = d.pop("run_id")

        session_id = d.pop("session_id")

        usage_record = cls(
            cached_tokens=cached_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            created_at=created_at,
            estimated=estimated,
            id=id,
            model=model,
            prompt_tokens=prompt_tokens,
            run_id=run_id,
            session_id=session_id,
        )

        usage_record.additional_properties = d
        return usage_record

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
