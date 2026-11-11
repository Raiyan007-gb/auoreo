from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.host_info import HostInfo


T = TypeVar("T", bound="HostList")


@_attrs_define
class HostList:
    """
    Attributes:
        enabled (bool): settings.m7.ssh.hosts: agents may use these hosts
        hosts (list[HostInfo]):
        problems (list[str] | Unset): Invalid or partly ignored host files
    """

    enabled: bool
    hosts: list[HostInfo]
    problems: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        enabled = self.enabled

        hosts = []
        for hosts_item_data in self.hosts:
            hosts_item = hosts_item_data.to_dict()
            hosts.append(hosts_item)

        problems: list[str] | Unset = UNSET
        if not isinstance(self.problems, Unset):
            problems = self.problems

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "enabled": enabled,
                "hosts": hosts,
            }
        )
        if problems is not UNSET:
            field_dict["problems"] = problems

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.host_info import HostInfo  # noqa: PLC0415

        d = dict(src_dict)
        enabled = d.pop("enabled")

        hosts = []
        _hosts = d.pop("hosts")
        for hosts_item_data in _hosts:
            hosts_item = HostInfo.from_dict(hosts_item_data)

            hosts.append(hosts_item)

        problems = cast(list[str], d.pop("problems", UNSET))

        host_list = cls(
            enabled=enabled,
            hosts=hosts,
            problems=problems,
        )

        host_list.additional_properties = d
        return host_list

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
