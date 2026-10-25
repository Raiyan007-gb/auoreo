from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="VoiceBackendStatus")


@_attrs_define
class VoiceBackendStatus:
    """
    Attributes:
        kind (str):
        ok (bool):
        detail (str | Unset):  Default: ''.
        license_ (str | Unset):  Default: ''.
        name (None | str | Unset):
        posture (str | Unset):  Default: 'ok'.
        remedy (str | Unset):  Default: ''.
    """

    kind: str
    ok: bool
    detail: str | Unset = ""
    license_: str | Unset = ""
    name: None | str | Unset = UNSET
    posture: str | Unset = "ok"
    remedy: str | Unset = ""
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind

        ok = self.ok

        detail = self.detail

        license_ = self.license_

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        posture = self.posture

        remedy = self.remedy

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
                "ok": ok,
            }
        )
        if detail is not UNSET:
            field_dict["detail"] = detail
        if license_ is not UNSET:
            field_dict["license"] = license_
        if name is not UNSET:
            field_dict["name"] = name
        if posture is not UNSET:
            field_dict["posture"] = posture
        if remedy is not UNSET:
            field_dict["remedy"] = remedy

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = d.pop("kind")

        ok = d.pop("ok")

        detail = d.pop("detail", UNSET)

        license_ = d.pop("license", UNSET)

        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))

        posture = d.pop("posture", UNSET)

        remedy = d.pop("remedy", UNSET)

        voice_backend_status = cls(
            kind=kind,
            ok=ok,
            detail=detail,
            license_=license_,
            name=name,
            posture=posture,
            remedy=remedy,
        )

        voice_backend_status.additional_properties = d
        return voice_backend_status

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
