from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.bandit_outcome_info_signals import BanditOutcomeInfoSignals


T = TypeVar("T", bound="BanditOutcomeInfo")


@_attrs_define
class BanditOutcomeInfo:
    """
    Attributes:
        arm_key (str):
        context_key (str):
        created_at (datetime.datetime):
        id (int):
        reward (float):
        run_id (str):
        session_id (str):
        signals (BanditOutcomeInfoSignals | Unset):
    """

    arm_key: str
    context_key: str
    created_at: datetime.datetime
    id: int
    reward: float
    run_id: str
    session_id: str
    signals: BanditOutcomeInfoSignals | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        arm_key = self.arm_key

        context_key = self.context_key

        created_at = self.created_at.isoformat()

        id = self.id

        reward = self.reward

        run_id = self.run_id

        session_id = self.session_id

        signals: dict[str, Any] | Unset = UNSET
        if not isinstance(self.signals, Unset):
            signals = self.signals.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "arm_key": arm_key,
                "context_key": context_key,
                "created_at": created_at,
                "id": id,
                "reward": reward,
                "run_id": run_id,
                "session_id": session_id,
            }
        )
        if signals is not UNSET:
            field_dict["signals"] = signals

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.bandit_outcome_info_signals import BanditOutcomeInfoSignals  # noqa: PLC0415

        d = dict(src_dict)
        arm_key = d.pop("arm_key")

        context_key = d.pop("context_key")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        reward = d.pop("reward")

        run_id = d.pop("run_id")

        session_id = d.pop("session_id")

        _signals = d.pop("signals", UNSET)
        signals: BanditOutcomeInfoSignals | Unset
        if isinstance(_signals, Unset):
            signals = UNSET
        else:
            signals = BanditOutcomeInfoSignals.from_dict(_signals)

        bandit_outcome_info = cls(
            arm_key=arm_key,
            context_key=context_key,
            created_at=created_at,
            id=id,
            reward=reward,
            run_id=run_id,
            session_id=session_id,
            signals=signals,
        )

        bandit_outcome_info.additional_properties = d
        return bandit_outcome_info

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
