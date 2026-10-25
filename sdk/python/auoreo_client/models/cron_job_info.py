from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.cron_run_info import CronRunInfo


T = TypeVar("T", bound="CronJobInfo")


@_attrs_define
class CronJobInfo:
    """
    Attributes:
        agent (str):
        deliver (list[str]):
        description (str):
        enabled (bool):
        last_run (CronRunInfo | None):
        name (str):
        next_run_at (datetime.datetime | None):
        paused (bool):
        schedule (str):
        tz (None | str):
        error (None | str | Unset): Schedule parse error, if any
    """

    agent: str
    deliver: list[str]
    description: str
    enabled: bool
    last_run: CronRunInfo | None
    name: str
    next_run_at: datetime.datetime | None
    paused: bool
    schedule: str
    tz: None | str
    error: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from ..models.cron_run_info import CronRunInfo  # noqa: PLC0415

        agent = self.agent

        deliver = self.deliver

        description = self.description

        enabled = self.enabled

        last_run: dict[str, Any] | None
        if isinstance(self.last_run, CronRunInfo):
            last_run = self.last_run.to_dict()
        else:
            last_run = self.last_run

        name = self.name

        next_run_at: None | str
        if isinstance(self.next_run_at, datetime.datetime):
            next_run_at = self.next_run_at.isoformat()
        else:
            next_run_at = self.next_run_at

        paused = self.paused

        schedule = self.schedule

        tz: None | str
        tz = self.tz

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "agent": agent,
                "deliver": deliver,
                "description": description,
                "enabled": enabled,
                "last_run": last_run,
                "name": name,
                "next_run_at": next_run_at,
                "paused": paused,
                "schedule": schedule,
                "tz": tz,
            }
        )
        if error is not UNSET:
            field_dict["error"] = error

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.cron_run_info import CronRunInfo  # noqa: PLC0415

        d = dict(src_dict)
        agent = d.pop("agent")

        deliver = cast(list[str], d.pop("deliver"))

        description = d.pop("description")

        enabled = d.pop("enabled")

        def _parse_last_run(data: object) -> CronRunInfo | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                last_run_type_0 = CronRunInfo.from_dict(data)

                return last_run_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CronRunInfo | None, data)

        last_run = _parse_last_run(d.pop("last_run"))

        name = d.pop("name")

        def _parse_next_run_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                next_run_at_type_0 = datetime.datetime.fromisoformat(data)

                return next_run_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        next_run_at = _parse_next_run_at(d.pop("next_run_at"))

        paused = d.pop("paused")

        schedule = d.pop("schedule")

        def _parse_tz(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        tz = _parse_tz(d.pop("tz"))

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        cron_job_info = cls(
            agent=agent,
            deliver=deliver,
            description=description,
            enabled=enabled,
            last_run=last_run,
            name=name,
            next_run_at=next_run_at,
            paused=paused,
            schedule=schedule,
            tz=tz,
            error=error,
        )

        cron_job_info.additional_properties = d
        return cron_job_info

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
