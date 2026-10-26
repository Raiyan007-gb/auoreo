from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.plan_step import PlanStep


T = TypeVar("T", bound="SessionPlan")


@_attrs_define
class SessionPlan:
    """The newest `update_plan` steps of a session (from `plan.updated` events).

    Attributes:
        steps (list[PlanStep]):
        explanation (str | Unset):  Default: ''.
        plan_mode (bool | Unset):  Default: False.
    """

    steps: list[PlanStep]
    explanation: str | Unset = ""
    plan_mode: bool | Unset = False
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        steps = []
        for steps_item_data in self.steps:
            steps_item = steps_item_data.to_dict()
            steps.append(steps_item)

        explanation = self.explanation

        plan_mode = self.plan_mode

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "steps": steps,
            }
        )
        if explanation is not UNSET:
            field_dict["explanation"] = explanation
        if plan_mode is not UNSET:
            field_dict["plan_mode"] = plan_mode

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.plan_step import PlanStep  # noqa: PLC0415

        d = dict(src_dict)
        steps = []
        _steps = d.pop("steps")
        for steps_item_data in _steps:
            steps_item = PlanStep.from_dict(steps_item_data)

            steps.append(steps_item)

        explanation = d.pop("explanation", UNSET)

        plan_mode = d.pop("plan_mode", UNSET)

        session_plan = cls(
            steps=steps,
            explanation=explanation,
            plan_mode=plan_mode,
        )

        session_plan.additional_properties = d
        return session_plan

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
