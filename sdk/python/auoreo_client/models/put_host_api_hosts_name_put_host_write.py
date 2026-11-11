from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.put_host_api_hosts_name_put_host_write_audit_commands import (
    PutHostApiHostsNamePutHostWriteAuditCommands,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="PutHostApiHostsNamePutHostWrite")


@_attrs_define
class PutHostApiHostsNamePutHostWrite:
    """The editable fields. A changed address or port is untrusted until `auoreo ssh test`.

    Attributes:
        host (str):
        key_name (str): A secret NAME, never key text
        user (str):
        audit_commands (PutHostApiHostsNamePutHostWriteAuditCommands | Unset):  Default:
            PutHostApiHostsNamePutHostWriteAuditCommands.HASH.
        description (str | Unset):  Default: ''.
        jump (None | str | Unset):
        keepalive_interval_s (float | Unset):  Default: 30.0.
        port (int | Unset):  Default: 22.
        tags (list[str] | Unset):
    """

    host: str
    key_name: str
    user: str
    audit_commands: PutHostApiHostsNamePutHostWriteAuditCommands | Unset = (
        PutHostApiHostsNamePutHostWriteAuditCommands.HASH
    )
    description: str | Unset = ""
    jump: None | str | Unset = UNSET
    keepalive_interval_s: float | Unset = 30.0
    port: int | Unset = 22
    tags: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        host = self.host

        key_name = self.key_name

        user = self.user

        audit_commands: str | Unset = UNSET
        if not isinstance(self.audit_commands, Unset):
            audit_commands = self.audit_commands.value

        description = self.description

        jump: None | str | Unset
        if isinstance(self.jump, Unset):
            jump = UNSET
        else:
            jump = self.jump

        keepalive_interval_s = self.keepalive_interval_s

        port = self.port

        tags: list[str] | Unset = UNSET
        if not isinstance(self.tags, Unset):
            tags = self.tags

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "host": host,
                "key_name": key_name,
                "user": user,
            }
        )
        if audit_commands is not UNSET:
            field_dict["audit_commands"] = audit_commands
        if description is not UNSET:
            field_dict["description"] = description
        if jump is not UNSET:
            field_dict["jump"] = jump
        if keepalive_interval_s is not UNSET:
            field_dict["keepalive_interval_s"] = keepalive_interval_s
        if port is not UNSET:
            field_dict["port"] = port
        if tags is not UNSET:
            field_dict["tags"] = tags

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        host = d.pop("host")

        key_name = d.pop("key_name")

        user = d.pop("user")

        _audit_commands = d.pop("audit_commands", UNSET)
        audit_commands: PutHostApiHostsNamePutHostWriteAuditCommands | Unset
        if isinstance(_audit_commands, Unset):
            audit_commands = UNSET
        else:
            audit_commands = PutHostApiHostsNamePutHostWriteAuditCommands(_audit_commands)

        description = d.pop("description", UNSET)

        def _parse_jump(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        jump = _parse_jump(d.pop("jump", UNSET))

        keepalive_interval_s = d.pop("keepalive_interval_s", UNSET)

        port = d.pop("port", UNSET)

        tags = cast(list[str], d.pop("tags", UNSET))

        put_host_api_hosts_name_put_host_write = cls(
            host=host,
            key_name=key_name,
            user=user,
            audit_commands=audit_commands,
            description=description,
            jump=jump,
            keepalive_interval_s=keepalive_interval_s,
            port=port,
            tags=tags,
        )

        return put_host_api_hosts_name_put_host_write
