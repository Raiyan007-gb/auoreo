from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="CronRunInfo")


@_attrs_define
class CronRunInfo:
    """
    Attributes:
        catch_up (bool):
        error (None | str):
        finished_at (datetime.datetime | None):
        id (int):
        job (str):
        scheduled_for (datetime.datetime):
        session_id (None | str):
        started_at (datetime.datetime | None):
        status (str): pending | running | ok | failed | skipped
    """

    catch_up: bool
    error: None | str
    finished_at: datetime.datetime | None
    id: int
    job: str
    scheduled_for: datetime.datetime
    session_id: None | str
    started_at: datetime.datetime | None
    status: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        catch_up = self.catch_up

        error: None | str
        error = self.error

        finished_at: None | str
        if isinstance(self.finished_at, datetime.datetime):
            finished_at = self.finished_at.isoformat()
        else:
            finished_at = self.finished_at

        id = self.id

        job = self.job

        scheduled_for = self.scheduled_for.isoformat()

        session_id: None | str
        session_id = self.session_id

        started_at: None | str
        if isinstance(self.started_at, datetime.datetime):
            started_at = self.started_at.isoformat()
        else:
            started_at = self.started_at

        status = self.status

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "catch_up": catch_up,
                "error": error,
                "finished_at": finished_at,
                "id": id,
                "job": job,
                "scheduled_for": scheduled_for,
                "session_id": session_id,
                "started_at": started_at,
                "status": status,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        catch_up = d.pop("catch_up")

        def _parse_error(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        error = _parse_error(d.pop("error"))

        def _parse_finished_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                finished_at_type_0 = datetime.datetime.fromisoformat(data)

                return finished_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        finished_at = _parse_finished_at(d.pop("finished_at"))

        id = d.pop("id")

        job = d.pop("job")

        scheduled_for = datetime.datetime.fromisoformat(d.pop("scheduled_for"))

        def _parse_session_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        session_id = _parse_session_id(d.pop("session_id"))

        def _parse_started_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                started_at_type_0 = datetime.datetime.fromisoformat(data)

                return started_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        started_at = _parse_started_at(d.pop("started_at"))

        status = d.pop("status")

        cron_run_info = cls(
            catch_up=catch_up,
            error=error,
            finished_at=finished_at,
            id=id,
            job=job,
            scheduled_for=scheduled_for,
            session_id=session_id,
            started_at=started_at,
            status=status,
        )

        cron_run_info.additional_properties = d
        return cron_run_info

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
