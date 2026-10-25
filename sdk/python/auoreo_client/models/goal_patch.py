from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.goal_patch_status_type_0 import GoalPatchStatusType0
from ..types import UNSET, Unset

T = TypeVar("T", bound="GoalPatch")


@_attrs_define
class GoalPatch:
    """
    Attributes:
        description (None | str | Unset):
        evidence_session_id (None | str | Unset): Append to evidence
        progress (float | None | Unset):
        status (GoalPatchStatusType0 | None | Unset):
        title (None | str | Unset):
    """

    description: None | str | Unset = UNSET
    evidence_session_id: None | str | Unset = UNSET
    progress: float | None | Unset = UNSET
    status: GoalPatchStatusType0 | None | Unset = UNSET
    title: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description: None | str | Unset
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        evidence_session_id: None | str | Unset
        if isinstance(self.evidence_session_id, Unset):
            evidence_session_id = UNSET
        else:
            evidence_session_id = self.evidence_session_id

        progress: float | None | Unset
        if isinstance(self.progress, Unset):
            progress = UNSET
        else:
            progress = self.progress

        status: None | str | Unset
        if isinstance(self.status, Unset):
            status = UNSET
        elif isinstance(self.status, GoalPatchStatusType0):
            status = self.status.value
        else:
            status = self.status

        title: None | str | Unset
        if isinstance(self.title, Unset):
            title = UNSET
        else:
            title = self.title

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({})
        if description is not UNSET:
            field_dict["description"] = description
        if evidence_session_id is not UNSET:
            field_dict["evidence_session_id"] = evidence_session_id
        if progress is not UNSET:
            field_dict["progress"] = progress
        if status is not UNSET:
            field_dict["status"] = status
        if title is not UNSET:
            field_dict["title"] = title

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_description(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_evidence_session_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        evidence_session_id = _parse_evidence_session_id(d.pop("evidence_session_id", UNSET))

        def _parse_progress(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        progress = _parse_progress(d.pop("progress", UNSET))

        def _parse_status(data: object) -> GoalPatchStatusType0 | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                status_type_0 = GoalPatchStatusType0(data)

                return status_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(GoalPatchStatusType0 | None | Unset, data)

        status = _parse_status(d.pop("status", UNSET))

        def _parse_title(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        title = _parse_title(d.pop("title", UNSET))

        goal_patch = cls(
            description=description,
            evidence_session_id=evidence_session_id,
            progress=progress,
            status=status,
            title=title,
        )

        goal_patch.additional_properties = d
        return goal_patch

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
