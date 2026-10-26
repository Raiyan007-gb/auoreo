from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.plan_step_status import PlanStepStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="PlanStep")


@_attrs_define
class PlanStep:
    """
    Attributes:
        step (str):
        done_when (None | str | Unset):
        status (PlanStepStatus | Unset):  Default: PlanStepStatus.PENDING.
    """

    step: str
    done_when: None | str | Unset = UNSET
    status: PlanStepStatus | Unset = PlanStepStatus.PENDING
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        step = self.step

        done_when: None | str | Unset
        if isinstance(self.done_when, Unset):
            done_when = UNSET
        else:
            done_when = self.done_when

        status: str | Unset = UNSET
        if not isinstance(self.status, Unset):
            status = self.status.value

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "step": step,
            }
        )
        if done_when is not UNSET:
            field_dict["done_when"] = done_when
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        step = d.pop("step")

        def _parse_done_when(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        done_when = _parse_done_when(d.pop("done_when", UNSET))

        _status = d.pop("status", UNSET)
        status: PlanStepStatus | Unset
        if isinstance(_status, Unset):
            status = UNSET
        else:
            status = PlanStepStatus(_status)

        plan_step = cls(
            step=step,
            done_when=done_when,
            status=status,
        )

        plan_step.additional_properties = d
        return plan_step

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
