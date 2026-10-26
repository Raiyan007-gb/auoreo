from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="WorkspaceTrust")


@_attrs_define
class WorkspaceTrust:
    """
    Attributes:
        trusted (bool):
        workspace (str): realpath of the server's working directory
        skipped (list[str] | Unset): `kind/name` definitions skipped from `<cwd>/.auoreo` (untrusted)
        trust_all (bool | Unset): AUOREO_SECURITY_TRUST_ALL_WORKSPACES is set Default: False.
    """

    trusted: bool
    workspace: str
    skipped: list[str] | Unset = UNSET
    trust_all: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        trusted = self.trusted

        workspace = self.workspace

        skipped: list[str] | Unset = UNSET
        if not isinstance(self.skipped, Unset):
            skipped = self.skipped

        trust_all = self.trust_all

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "trusted": trusted,
                "workspace": workspace,
            }
        )
        if skipped is not UNSET:
            field_dict["skipped"] = skipped
        if trust_all is not UNSET:
            field_dict["trust_all"] = trust_all

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        trusted = d.pop("trusted")

        workspace = d.pop("workspace")

        skipped = cast(list[str], d.pop("skipped", UNSET))

        trust_all = d.pop("trust_all", UNSET)

        workspace_trust = cls(
            trusted=trusted,
            workspace=workspace,
            skipped=skipped,
            trust_all=trust_all,
        )

        workspace_trust.additional_properties = d
        return workspace_trust

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
