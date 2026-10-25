from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..models.bandit_status_contexts_mode import BanditStatusContextsMode

if TYPE_CHECKING:
    from ..models.bandit_arm_info import BanditArmInfo
    from ..models.bandit_arm_stats import BanditArmStats
    from ..models.bandit_context_info import BanditContextInfo
    from ..models.bandit_outcome_info import BanditOutcomeInfo
    from ..models.bandit_status_weights import BanditStatusWeights


T = TypeVar("T", bound="BanditStatus")


@_attrs_define
class BanditStatus:
    """
    Attributes:
        arms (list[BanditArmInfo]):
        contexts (list[BanditContextInfo]):
        contexts_mode (BanditStatusContextsMode):
        enabled (bool):
        epsilon (float):
        epsilon_decay (float):
        min_epsilon (float):
        recent (list[BanditOutcomeInfo]):
        stats (list[BanditArmStats]):
        weights (BanditStatusWeights):
    """

    arms: list[BanditArmInfo]
    contexts: list[BanditContextInfo]
    contexts_mode: BanditStatusContextsMode
    enabled: bool
    epsilon: float
    epsilon_decay: float
    min_epsilon: float
    recent: list[BanditOutcomeInfo]
    stats: list[BanditArmStats]
    weights: BanditStatusWeights
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        arms = []
        for arms_item_data in self.arms:
            arms_item = arms_item_data.to_dict()
            arms.append(arms_item)

        contexts = []
        for contexts_item_data in self.contexts:
            contexts_item = contexts_item_data.to_dict()
            contexts.append(contexts_item)

        contexts_mode = self.contexts_mode.value

        enabled = self.enabled

        epsilon = self.epsilon

        epsilon_decay = self.epsilon_decay

        min_epsilon = self.min_epsilon

        recent = []
        for recent_item_data in self.recent:
            recent_item = recent_item_data.to_dict()
            recent.append(recent_item)

        stats = []
        for stats_item_data in self.stats:
            stats_item = stats_item_data.to_dict()
            stats.append(stats_item)

        weights = self.weights.to_dict()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "arms": arms,
                "contexts": contexts,
                "contexts_mode": contexts_mode,
                "enabled": enabled,
                "epsilon": epsilon,
                "epsilon_decay": epsilon_decay,
                "min_epsilon": min_epsilon,
                "recent": recent,
                "stats": stats,
                "weights": weights,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.bandit_arm_info import BanditArmInfo  # noqa: PLC0415
        from ..models.bandit_arm_stats import BanditArmStats  # noqa: PLC0415
        from ..models.bandit_context_info import BanditContextInfo  # noqa: PLC0415
        from ..models.bandit_outcome_info import BanditOutcomeInfo  # noqa: PLC0415
        from ..models.bandit_status_weights import BanditStatusWeights  # noqa: PLC0415

        d = dict(src_dict)
        arms = []
        _arms = d.pop("arms")
        for arms_item_data in _arms:
            arms_item = BanditArmInfo.from_dict(arms_item_data)

            arms.append(arms_item)

        contexts = []
        _contexts = d.pop("contexts")
        for contexts_item_data in _contexts:
            contexts_item = BanditContextInfo.from_dict(contexts_item_data)

            contexts.append(contexts_item)

        contexts_mode = BanditStatusContextsMode(d.pop("contexts_mode"))

        enabled = d.pop("enabled")

        epsilon = d.pop("epsilon")

        epsilon_decay = d.pop("epsilon_decay")

        min_epsilon = d.pop("min_epsilon")

        recent = []
        _recent = d.pop("recent")
        for recent_item_data in _recent:
            recent_item = BanditOutcomeInfo.from_dict(recent_item_data)

            recent.append(recent_item)

        stats = []
        _stats = d.pop("stats")
        for stats_item_data in _stats:
            stats_item = BanditArmStats.from_dict(stats_item_data)

            stats.append(stats_item)

        weights = BanditStatusWeights.from_dict(d.pop("weights"))

        bandit_status = cls(
            arms=arms,
            contexts=contexts,
            contexts_mode=contexts_mode,
            enabled=enabled,
            epsilon=epsilon,
            epsilon_decay=epsilon_decay,
            min_epsilon=min_epsilon,
            recent=recent,
            stats=stats,
            weights=weights,
        )

        bandit_status.additional_properties = d
        return bandit_status

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
