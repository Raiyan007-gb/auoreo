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
        schedule (str): cron | every <n>m|h|d | <n>m|h|d | every monday 9am [tz] | in <n>m|h|d | ISO datetime
        agent (str | Unset):  Default: 'default'.
        catch_up (bool | Unset):  Default: True.
        context_from (list[str] | Unset): previous | <job name>
        deliver (list[str] | Unset):
        description (None | str | Unset):
        dry_run (bool | Unset): Validate and preview; nothing is saved Default: False.
        enabled (bool | Unset):  Default: True.
        footer (None | str | Unset): Delivery footer ("" disables)
        header (None | str | Unset): Delivery header ("" disables)
        model (None | str | Unset):
        provider (None | str | Unset):
        script (None | str | Unset): Pre-run script under <home>/scripts/
        timeout_s (int | None | Unset):
        tz (None | str | Unset):
        workdir (None | str | Unset):
    """

    name: str
    prompt: str
    schedule: str
    agent: str | Unset = "default"
    catch_up: bool | Unset = True
    context_from: list[str] | Unset = UNSET
    deliver: list[str] | Unset = UNSET
    description: None | str | Unset = UNSET
    dry_run: bool | Unset = False
    enabled: bool | Unset = True
    footer: None | str | Unset = UNSET
    header: None | str | Unset = UNSET
    model: None | str | Unset = UNSET
    provider: None | str | Unset = UNSET
    script: None | str | Unset = UNSET
    timeout_s: int | None | Unset = UNSET
    tz: None | str | Unset = UNSET
    workdir: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        prompt = self.prompt

        schedule = self.schedule

        agent = self.agent

        catch_up = self.catch_up

        context_from: list[str] | Unset = UNSET
        if not isinstance(self.context_from, Unset):
            context_from = self.context_from

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

        footer: None | str | Unset
        if isinstance(self.footer, Unset):
            footer = UNSET
        else:
            footer = self.footer

        header: None | str | Unset
        if isinstance(self.header, Unset):
            header = UNSET
        else:
            header = self.header

        model: None | str | Unset
        if isinstance(self.model, Unset):
            model = UNSET
        else:
            model = self.model

        provider: None | str | Unset
        if isinstance(self.provider, Unset):
            provider = UNSET
        else:
            provider = self.provider

        script: None | str | Unset
        if isinstance(self.script, Unset):
            script = UNSET
        else:
            script = self.script

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

        workdir: None | str | Unset
        if isinstance(self.workdir, Unset):
            workdir = UNSET
        else:
            workdir = self.workdir

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
        if context_from is not UNSET:
            field_dict["context_from"] = context_from
        if deliver is not UNSET:
            field_dict["deliver"] = deliver
        if description is not UNSET:
            field_dict["description"] = description
        if dry_run is not UNSET:
            field_dict["dry_run"] = dry_run
        if enabled is not UNSET:
            field_dict["enabled"] = enabled
        if footer is not UNSET:
            field_dict["footer"] = footer
        if header is not UNSET:
            field_dict["header"] = header
        if model is not UNSET:
            field_dict["model"] = model
        if provider is not UNSET:
            field_dict["provider"] = provider
        if script is not UNSET:
            field_dict["script"] = script
        if timeout_s is not UNSET:
            field_dict["timeout_s"] = timeout_s
        if tz is not UNSET:
            field_dict["tz"] = tz
        if workdir is not UNSET:
            field_dict["workdir"] = workdir

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        prompt = d.pop("prompt")

        schedule = d.pop("schedule")

        agent = d.pop("agent", UNSET)

        catch_up = d.pop("catch_up", UNSET)

        context_from = cast(list[str], d.pop("context_from", UNSET))

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

        def _parse_footer(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        footer = _parse_footer(d.pop("footer", UNSET))

        def _parse_header(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        header = _parse_header(d.pop("header", UNSET))

        def _parse_model(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        model = _parse_model(d.pop("model", UNSET))

        def _parse_provider(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        provider = _parse_provider(d.pop("provider", UNSET))

        def _parse_script(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        script = _parse_script(d.pop("script", UNSET))

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

        def _parse_workdir(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        workdir = _parse_workdir(d.pop("workdir", UNSET))

        cron_create = cls(
            name=name,
            prompt=prompt,
            schedule=schedule,
            agent=agent,
            catch_up=catch_up,
            context_from=context_from,
            deliver=deliver,
            description=description,
            dry_run=dry_run,
            enabled=enabled,
            footer=footer,
            header=header,
            model=model,
            provider=provider,
            script=script,
            timeout_s=timeout_s,
            tz=tz,
            workdir=workdir,
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
