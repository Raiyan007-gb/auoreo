from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.hub_installed_skill_integrity import HubInstalledSkillIntegrity
from ..models.hub_installed_skill_status import HubInstalledSkillStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="HubInstalledSkill")


@_attrs_define
class HubInstalledSkill:
    """
    Attributes:
        installed_at (float):
        integrity (HubInstalledSkillIntegrity):
        name (str):
        sha256 (str):
        status (HubInstalledSkillStatus):
        tap (str):
        approved_at (float | None | Unset):
        files (list[str] | Unset):
        has_scripts (bool | Unset):  Default: False.
        lint (list[str] | Unset):
        version (str | Unset):  Default: ''.
    """

    installed_at: float
    integrity: HubInstalledSkillIntegrity
    name: str
    sha256: str
    status: HubInstalledSkillStatus
    tap: str
    approved_at: float | None | Unset = UNSET
    files: list[str] | Unset = UNSET
    has_scripts: bool | Unset = False
    lint: list[str] | Unset = UNSET
    version: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        installed_at = self.installed_at

        integrity = self.integrity.value

        name = self.name

        sha256 = self.sha256

        status = self.status.value

        tap = self.tap

        approved_at: float | None | Unset
        if isinstance(self.approved_at, Unset):
            approved_at = UNSET
        else:
            approved_at = self.approved_at

        files: list[str] | Unset = UNSET
        if not isinstance(self.files, Unset):
            files = self.files

        has_scripts = self.has_scripts

        lint: list[str] | Unset = UNSET
        if not isinstance(self.lint, Unset):
            lint = self.lint

        version = self.version

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "installed_at": installed_at,
                "integrity": integrity,
                "name": name,
                "sha256": sha256,
                "status": status,
                "tap": tap,
            }
        )
        if approved_at is not UNSET:
            field_dict["approved_at"] = approved_at
        if files is not UNSET:
            field_dict["files"] = files
        if has_scripts is not UNSET:
            field_dict["has_scripts"] = has_scripts
        if lint is not UNSET:
            field_dict["lint"] = lint
        if version is not UNSET:
            field_dict["version"] = version

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        installed_at = d.pop("installed_at")

        integrity = HubInstalledSkillIntegrity(d.pop("integrity"))

        name = d.pop("name")

        sha256 = d.pop("sha256")

        status = HubInstalledSkillStatus(d.pop("status"))

        tap = d.pop("tap")

        def _parse_approved_at(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        approved_at = _parse_approved_at(d.pop("approved_at", UNSET))

        files = cast(list[str], d.pop("files", UNSET))

        has_scripts = d.pop("has_scripts", UNSET)

        lint = cast(list[str], d.pop("lint", UNSET))

        version = d.pop("version", UNSET)

        hub_installed_skill = cls(
            installed_at=installed_at,
            integrity=integrity,
            name=name,
            sha256=sha256,
            status=status,
            tap=tap,
            approved_at=approved_at,
            files=files,
            has_scripts=has_scripts,
            lint=lint,
            version=version,
        )

        hub_installed_skill.additional_properties = d
        return hub_installed_skill

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
