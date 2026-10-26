from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.hub_installed_skill import HubInstalledSkill
    from ..models.mcp_skill_source import McpSkillSource


T = TypeVar("T", bound="SkillSources")


@_attrs_define
class SkillSources:
    """
    Attributes:
        installed (list[HubInstalledSkill]):
        mcp (list[McpSkillSource]):
        taps (list[str]):
    """

    installed: list[HubInstalledSkill]
    mcp: list[McpSkillSource]
    taps: list[str]
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        installed = []
        for installed_item_data in self.installed:
            installed_item = installed_item_data.to_dict()
            installed.append(installed_item)

        mcp = []
        for mcp_item_data in self.mcp:
            mcp_item = mcp_item_data.to_dict()
            mcp.append(mcp_item)

        taps = self.taps

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "installed": installed,
                "mcp": mcp,
                "taps": taps,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.hub_installed_skill import HubInstalledSkill  # noqa: PLC0415
        from ..models.mcp_skill_source import McpSkillSource  # noqa: PLC0415

        d = dict(src_dict)
        installed = []
        _installed = d.pop("installed")
        for installed_item_data in _installed:
            installed_item = HubInstalledSkill.from_dict(installed_item_data)

            installed.append(installed_item)

        mcp = []
        _mcp = d.pop("mcp")
        for mcp_item_data in _mcp:
            mcp_item = McpSkillSource.from_dict(mcp_item_data)

            mcp.append(mcp_item)

        taps = cast(list[str], d.pop("taps"))

        skill_sources = cls(
            installed=installed,
            mcp=mcp,
            taps=taps,
        )

        skill_sources.additional_properties = d
        return skill_sources

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
