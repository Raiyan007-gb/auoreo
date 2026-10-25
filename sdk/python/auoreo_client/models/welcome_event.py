from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.welcome_event_action import WelcomeEventAction
from ..models.welcome_event_step import WelcomeEventStep

T = TypeVar("T", bound="WelcomeEvent")


@_attrs_define
class WelcomeEvent:
    """One onboarding step outcome; recorded in the audit log as `welcome.<action>`.

    Attributes:
        action (WelcomeEventAction):
        step (WelcomeEventStep):
    """

    action: WelcomeEventAction
    step: WelcomeEventStep

    def to_dict(self) -> dict[str, Any]:
        action = self.action.value

        step = self.step.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "action": action,
                "step": step,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        action = WelcomeEventAction(d.pop("action"))

        step = WelcomeEventStep(d.pop("step"))

        welcome_event = cls(
            action=action,
            step=step,
        )

        return welcome_event
