from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ReembedStatus")


@_attrs_define
class ReembedStatus:
    """
    Attributes:
        embed_model (str):
        state (str): idle | running | done | failed
        done (int | Unset):  Default: 0.
        error (None | str | Unset):
        stale (int | Unset): rows whose stored embed_model differs from the current one Default: 0.
        total (int | Unset):  Default: 0.
    """

    embed_model: str
    state: str
    done: int | Unset = 0
    error: None | str | Unset = UNSET
    stale: int | Unset = 0
    total: int | Unset = 0
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        embed_model = self.embed_model

        state = self.state

        done = self.done

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        stale = self.stale

        total = self.total

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "embed_model": embed_model,
                "state": state,
            }
        )
        if done is not UNSET:
            field_dict["done"] = done
        if error is not UNSET:
            field_dict["error"] = error
        if stale is not UNSET:
            field_dict["stale"] = stale
        if total is not UNSET:
            field_dict["total"] = total

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        embed_model = d.pop("embed_model")

        state = d.pop("state")

        done = d.pop("done", UNSET)

        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))

        stale = d.pop("stale", UNSET)

        total = d.pop("total", UNSET)

        reembed_status = cls(
            embed_model=embed_model,
            state=state,
            done=done,
            error=error,
            stale=stale,
            total=total,
        )

        reembed_status.additional_properties = d
        return reembed_status

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
