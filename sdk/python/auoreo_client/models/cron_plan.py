from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="CronPlan")


@_attrs_define
class CronPlan:
    """
    Attributes:
        catch_up (str): Catch-up class for a missed fire
        created (bool):
        dry_run (bool):
        kind (str): cron | interval | date
        name (str):
        next_fires (list[str]): Next (up to) 5 fire times, ISO-8601
        schedule (str):
        tz (str):
    """

    catch_up: str
    created: bool
    dry_run: bool
    kind: str
    name: str
    next_fires: list[str]
    schedule: str
    tz: str
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        catch_up = self.catch_up

        created = self.created

        dry_run = self.dry_run

        kind = self.kind

        name = self.name

        next_fires = self.next_fires

        schedule = self.schedule

        tz = self.tz

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "catch_up": catch_up,
                "created": created,
                "dry_run": dry_run,
                "kind": kind,
                "name": name,
                "next_fires": next_fires,
                "schedule": schedule,
                "tz": tz,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        catch_up = d.pop("catch_up")

        created = d.pop("created")

        dry_run = d.pop("dry_run")

        kind = d.pop("kind")

        name = d.pop("name")

        next_fires = cast(list[str], d.pop("next_fires"))

        schedule = d.pop("schedule")

        tz = d.pop("tz")

        cron_plan = cls(
            catch_up=catch_up,
            created=created,
            dry_run=dry_run,
            kind=kind,
            name=name,
            next_fires=next_fires,
            schedule=schedule,
            tz=tz,
        )

        cron_plan.additional_properties = d
        return cron_plan

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
