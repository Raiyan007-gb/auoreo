from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

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
        cache_write_tokens (int | Unset):  Default: 0.
        deployment (None | str | Unset):
        generation_id (None | str | Unset):
        reasoning_tokens (int | Unset):  Default: 0.
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
    cache_write_tokens: int | Unset = 0
    deployment: None | str | Unset = UNSET
    generation_id: None | str | Unset = UNSET
    reasoning_tokens: int | Unset = 0
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

        cache_write_tokens = self.cache_write_tokens

        deployment: None | str | Unset
        if isinstance(self.deployment, Unset):
            deployment = UNSET
        else:
            deployment = self.deployment

        generation_id: None | str | Unset
        if isinstance(self.generation_id, Unset):
            generation_id = UNSET
        else:
            generation_id = self.generation_id

        reasoning_tokens = self.reasoning_tokens

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
        if cache_write_tokens is not UNSET:
            field_dict["cache_write_tokens"] = cache_write_tokens
        if deployment is not UNSET:
            field_dict["deployment"] = deployment
        if generation_id is not UNSET:
            field_dict["generation_id"] = generation_id
        if reasoning_tokens is not UNSET:
            field_dict["reasoning_tokens"] = reasoning_tokens

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

        cache_write_tokens = d.pop("cache_write_tokens", UNSET)

        def _parse_deployment(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        deployment = _parse_deployment(d.pop("deployment", UNSET))

        def _parse_generation_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        generation_id = _parse_generation_id(d.pop("generation_id", UNSET))

        reasoning_tokens = d.pop("reasoning_tokens", UNSET)

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
            cache_write_tokens=cache_write_tokens,
            deployment=deployment,
            generation_id=generation_id,
            reasoning_tokens=reasoning_tokens,
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
