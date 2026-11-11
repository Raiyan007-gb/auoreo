from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.host_info_audit_commands import HostInfoAuditCommands
from ..types import UNSET, Unset

T = TypeVar("T", bound="HostInfo")


@_attrs_define
class HostInfo:
    """
    Attributes:
        host (str):
        key_name (str): NAME of the stored secret holding the private key
        key_present (bool): The secret store has a value under key_name
        name (str):
        port (int):
        trusted (bool): known_hosts has a host key for host:port (`auoreo ssh test`)
        usable_by_agents (bool): m7.ssh.hosts is on, the host is trusted and not shadowed
        user (str):
        audit_commands (HostInfoAuditCommands | Unset):  Default: HostInfoAuditCommands.HASH.
        description (str | Unset):  Default: ''.
        host_key_fingerprints (list[str] | Unset):
        jump (None | str | Unset):
        keepalive_interval_s (float | Unset):  Default: 30.0.
        key_fingerprint (None | str | Unset): SHA256 fingerprint (public), if known
        problem (None | str | Unset): Why agents cannot use it (a broken jump chain, …)
        shadowed (bool | Unset): A settings.environments entry has the same name Default: False.
        tags (list[str] | Unset):
    """

    host: str
    key_name: str
    key_present: bool
    name: str
    port: int
    trusted: bool
    usable_by_agents: bool
    user: str
    audit_commands: HostInfoAuditCommands | Unset = HostInfoAuditCommands.HASH
    description: str | Unset = ""
    host_key_fingerprints: list[str] | Unset = UNSET
    jump: None | str | Unset = UNSET
    keepalive_interval_s: float | Unset = 30.0
    key_fingerprint: None | str | Unset = UNSET
    problem: None | str | Unset = UNSET
    shadowed: bool | Unset = False
    tags: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        host = self.host

        key_name = self.key_name

        key_present = self.key_present

        name = self.name

        port = self.port

        trusted = self.trusted

        usable_by_agents = self.usable_by_agents

        user = self.user

        audit_commands: str | Unset = UNSET
        if not isinstance(self.audit_commands, Unset):
            audit_commands = self.audit_commands.value

        description = self.description

        host_key_fingerprints: list[str] | Unset = UNSET
        if not isinstance(self.host_key_fingerprints, Unset):
            host_key_fingerprints = self.host_key_fingerprints

        jump: None | str | Unset
        if isinstance(self.jump, Unset):
            jump = UNSET
        else:
            jump = self.jump

        keepalive_interval_s = self.keepalive_interval_s

        key_fingerprint: None | str | Unset
        if isinstance(self.key_fingerprint, Unset):
            key_fingerprint = UNSET
        else:
            key_fingerprint = self.key_fingerprint

        problem: None | str | Unset
        if isinstance(self.problem, Unset):
            problem = UNSET
        else:
            problem = self.problem

        shadowed = self.shadowed

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "host": host,
                "key_name": key_name,
                "key_present": key_present,
                "name": name,
                "port": port,
                "trusted": trusted,
                "usable_by_agents": usable_by_agents,
                "user": user,
            }
        )
        if audit_commands is not UNSET:
            field_dict["audit_commands"] = audit_commands
        if description is not UNSET:
            field_dict["description"] = description
        if host_key_fingerprints is not UNSET:
            field_dict["host_key_fingerprints"] = host_key_fingerprints
        if jump is not UNSET:
            field_dict["jump"] = jump
        if keepalive_interval_s is not UNSET:
            field_dict["keepalive_interval_s"] = keepalive_interval_s
        if key_fingerprint is not UNSET:
            field_dict["key_fingerprint"] = key_fingerprint
        if problem is not UNSET:
            field_dict["problem"] = problem
        if shadowed is not UNSET:
            field_dict["shadowed"] = shadowed
        if tags is not UNSET:
            field_dict["tags"] = tags

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        host = d.pop("host")

        key_name = d.pop("key_name")

        key_present = d.pop("key_present")

        name = d.pop("name")

        port = d.pop("port")

        trusted = d.pop("trusted")

        usable_by_agents = d.pop("usable_by_agents")

        user = d.pop("user")

        _audit_commands = d.pop("audit_commands", UNSET)
        audit_commands: HostInfoAuditCommands | Unset
        if isinstance(_audit_commands, Unset):
            audit_commands = UNSET
        else:
            audit_commands = HostInfoAuditCommands(_audit_commands)

        description = d.pop("description", UNSET)

        host_key_fingerprints = cast(list[str], d.pop("host_key_fingerprints", UNSET))

        def _parse_jump(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        jump = _parse_jump(d.pop("jump", UNSET))

        keepalive_interval_s = d.pop("keepalive_interval_s", UNSET)

        def _parse_key_fingerprint(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        key_fingerprint = _parse_key_fingerprint(d.pop("key_fingerprint", UNSET))

        def _parse_problem(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        problem = _parse_problem(d.pop("problem", UNSET))

        shadowed = d.pop("shadowed", UNSET)

        tags = cast(list[str], d.pop("tags", UNSET))

        host_info = cls(
            host=host,
            key_name=key_name,
            key_present=key_present,
            name=name,
            port=port,
            trusted=trusted,
            usable_by_agents=usable_by_agents,
            user=user,
            audit_commands=audit_commands,
            description=description,
            host_key_fingerprints=host_key_fingerprints,
            jump=jump,
            keepalive_interval_s=keepalive_interval_s,
            key_fingerprint=key_fingerprint,
            problem=problem,
            shadowed=shadowed,
            tags=tags,
        )

        host_info.additional_properties = d
        return host_info

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
