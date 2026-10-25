from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.environment_info_kind import EnvironmentInfoKind
from ..types import UNSET, Unset

T = TypeVar("T", bound="EnvironmentInfo")


@_attrs_define
class EnvironmentInfo:
    """One place commands (or mail) can go. Never carries a secret.

    Attributes:
        kind (EnvironmentInfoKind):
        name (str):
        backend (None | str | Unset):
        detail (str | Unset):  Default: ''.
        ok (bool | None | Unset): check() result; null when not checked
        remote (bool | Unset):  Default: False.
        selected (bool | Unset):  Default: False.
        url (None | str | Unset): Endpoint without credentials, query or fragment
    """

    kind: EnvironmentInfoKind
    name: str
    backend: None | str | Unset = UNSET
    detail: str | Unset = ""
    ok: bool | None | Unset = UNSET
    remote: bool | Unset = False
    selected: bool | Unset = False
    url: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        kind = self.kind.value

        name = self.name

        backend: None | str | Unset
        if isinstance(self.backend, Unset):
            backend = UNSET
        else:
            backend = self.backend

        detail = self.detail

        ok: bool | None | Unset
        if isinstance(self.ok, Unset):
            ok = UNSET
        else:
            ok = self.ok

        remote = self.remote

        selected = self.selected

        url: None | str | Unset
        if isinstance(self.url, Unset):
            url = UNSET
        else:
            url = self.url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "kind": kind,
                "name": name,
            }
        )
        if backend is not UNSET:
            field_dict["backend"] = backend
        if detail is not UNSET:
            field_dict["detail"] = detail
        if ok is not UNSET:
            field_dict["ok"] = ok
        if remote is not UNSET:
            field_dict["remote"] = remote
        if selected is not UNSET:
            field_dict["selected"] = selected
        if url is not UNSET:
            field_dict["url"] = url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        kind = EnvironmentInfoKind(d.pop("kind"))

        name = d.pop("name")

        def _parse_backend(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        backend = _parse_backend(d.pop("backend", UNSET))

        detail = d.pop("detail", UNSET)

        def _parse_ok(data: object) -> bool | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(bool | None | Unset, data)

        ok = _parse_ok(d.pop("ok", UNSET))

        remote = d.pop("remote", UNSET)

        selected = d.pop("selected", UNSET)

        def _parse_url(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        url = _parse_url(d.pop("url", UNSET))

        environment_info = cls(
            kind=kind,
            name=name,
            backend=backend,
            detail=detail,
            ok=ok,
            remote=remote,
            selected=selected,
            url=url,
        )

        environment_info.additional_properties = d
        return environment_info

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
