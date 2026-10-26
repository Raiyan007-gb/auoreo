from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.custom_var_out import CustomVarOut
    from ..models.env_section_out import EnvSectionOut


T = TypeVar("T", bound="EnvListing")


@_attrs_define
class EnvListing:
    """
    Attributes:
        custom (list[CustomVarOut]):
        dotenv_exists (bool):
        dotenv_path (str):
        history (int): settings.json versions available to undo
        sections (list[EnvSectionOut]):
        pending_restart (list[str] | Unset):
    """

    custom: list[CustomVarOut]
    dotenv_exists: bool
    dotenv_path: str
    history: int
    sections: list[EnvSectionOut]
    pending_restart: list[str] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        custom = []
        for custom_item_data in self.custom:
            custom_item = custom_item_data.to_dict()
            custom.append(custom_item)

        dotenv_exists = self.dotenv_exists

        dotenv_path = self.dotenv_path

        history = self.history

        sections = []
        for sections_item_data in self.sections:
            sections_item = sections_item_data.to_dict()
            sections.append(sections_item)

        pending_restart: list[str] | Unset = UNSET
        if not isinstance(self.pending_restart, Unset):
            pending_restart = self.pending_restart

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "custom": custom,
                "dotenv_exists": dotenv_exists,
                "dotenv_path": dotenv_path,
                "history": history,
                "sections": sections,
            }
        )
        if pending_restart is not UNSET:
            field_dict["pending_restart"] = pending_restart

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.custom_var_out import CustomVarOut  # noqa: PLC0415
        from ..models.env_section_out import EnvSectionOut  # noqa: PLC0415

        d = dict(src_dict)
        custom = []
        _custom = d.pop("custom")
        for custom_item_data in _custom:
            custom_item = CustomVarOut.from_dict(custom_item_data)

            custom.append(custom_item)

        dotenv_exists = d.pop("dotenv_exists")

        dotenv_path = d.pop("dotenv_path")

        history = d.pop("history")

        sections = []
        _sections = d.pop("sections")
        for sections_item_data in _sections:
            sections_item = EnvSectionOut.from_dict(sections_item_data)

            sections.append(sections_item)

        pending_restart = cast(list[str], d.pop("pending_restart", UNSET))

        env_listing = cls(
            custom=custom,
            dotenv_exists=dotenv_exists,
            dotenv_path=dotenv_path,
            history=history,
            sections=sections,
            pending_restart=pending_restart,
        )

        env_listing.additional_properties = d
        return env_listing

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
