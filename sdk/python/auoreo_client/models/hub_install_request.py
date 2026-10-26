from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="HubInstallRequest")


@_attrs_define
class HubInstallRequest:
    """
    Attributes:
        name (str):
        force (bool | Unset): Reinstall (goes back to quarantine). Default: False.
        tap (None | str | Unset): Only look in this configured tap.
    """

    name: str
    force: bool | Unset = False
    tap: None | str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        force = self.force

        tap: None | str | Unset
        if isinstance(self.tap, Unset):
            tap = UNSET
        else:
            tap = self.tap

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
            }
        )
        if force is not UNSET:
            field_dict["force"] = force
        if tap is not UNSET:
            field_dict["tap"] = tap

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        force = d.pop("force", UNSET)

        def _parse_tap(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        tap = _parse_tap(d.pop("tap", UNSET))

        hub_install_request = cls(
            name=name,
            force=force,
            tap=tap,
        )

        return hub_install_request
