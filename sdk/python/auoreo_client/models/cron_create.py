from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="CronCreate")


@_attrs_define
class CronCreate:
    """
    Attributes:
        name (str):
        prompt (str):
        schedule (str): 5-field cron | every <n>m|h|d | in <n>m|h|d | ISO datetime
        agent (str | Unset):  Default: 'default'.
        catch_up (bool | Unset):  Default: True.
        deliver (list[str] | Unset):
        description (None | str | Unset):
        dry_run (bool | Unset): Validate and preview; nothing is saved Default: False.
        enabled (bool | Unset):  Default: True.
        timeout_s (int | None | Unset):
        tz (None | str | Unset):
    """

    name: str
    prompt: str
    schedule: str
    agent: str | Unset = "default"
    catch_up: bool | Unset = True
    deliver: list[str] | Unset = UNSET
    description: None | str | Unset = UNSET
    dry_run: bool | Unset = False
    enabled: bool | Unset = True
    timeout_s: int | None | Unset = UNSET
    tz: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        prompt = self.prompt

        schedule = self.schedule

        agent = self.agent

        catch_up = self.catch_up

        deliver: list[str] | Unset = UNSET
        if not isinstance(self.deliver, Unset):
            deliver = self.deliver

        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        dry_run = self.dry_run

        enabled = self.enabled

        timeout_s: int | None | Unset
        if isinstance(self.timeout_s, Unset):
            timeout_s = UNSET
        else:
            timeout_s = self.timeout_s

        tz: None | str | Unset
        if isinstance(self.tz, Unset):
            tz = UNSET
        else:
            tz = self.tz

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "name": name,
                "prompt": prompt,
                "schedule": schedule,
            }
        )
        if agent is not UNSET:
            field_dict["agent"] = agent
        if catch_up is not UNSET:
            field_dict["catch_up"] = catch_up
        if deliver is not UNSET:
            field_dict["deliver"] = deliver
        if description is not UNSET:
            field_dict["description"] = description
        if dry_run is not UNSET:
            field_dict["dry_run"] = dry_run
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if timeout_s is not UNSET:
            field_dict["timeout_s"] = timeout_s
        if tz is not UNSET:
            field_dict["tz"] = tz

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        prompt = d.pop("prompt")

        schedule = d.pop("schedule")

        agent = d.pop("agent", UNSET)

        catch_up = d.pop("catch_up", UNSET)

        deliver = cast(list[str], d.pop("deliver", UNSET))

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        dry_run = d.pop("dry_run", UNSET)

        enabled = d.pop("enabled", UNSET)

        def _parse_timeout_s(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        timeout_s = _parse_timeout_s(d.pop("timeout_s", UNSET))

        def _parse_tz(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tz = _parse_tz(d.pop("tz", UNSET))

        cron_create = cls(
            name=name,
            prompt=prompt,
            schedule=schedule,
            agent=agent,
            catch_up=catch_up,
            deliver=deliver,
            description=description,
            dry_run=dry_run,
            enabled=enabled,
            timeout_s=timeout_s,
            tz=tz,
        )

        cron_create.additional_properties = d
        return cron_create

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
