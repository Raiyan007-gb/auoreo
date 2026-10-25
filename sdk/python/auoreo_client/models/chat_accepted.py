from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="ChatAccepted")


@_attrs_define
class ChatAccepted:
    """
    Attributes:
        run_id (None | str):
        session_id (str):
        deduped (bool | Unset):  Default: False.
    """

    run_id: None | str
    session_id: str
    deduped: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        run_id: None | str
        run_id = self.run_id

        session_id = self.session_id

        deduped = self.deduped

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "run_id": run_id,
                "session_id": session_id,
            }
        )
        if deduped is not UNSET:
            field_dict["deduped"] = deduped

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_run_id(data: object) -> None | str:
            if data is None:
                return data
            return cast(None | str, data)

        run_id = _parse_run_id(d.pop("run_id"))

        session_id = d.pop("session_id")

        deduped = d.pop("deduped", UNSET)

        chat_accepted = cls(
            run_id=run_id,
            session_id=session_id,
            deduped=deduped,
        )

        chat_accepted.additional_properties = d
        return chat_accepted

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
