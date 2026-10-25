from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.curator_contradiction import CuratorContradiction
    from ..models.curator_merge import CuratorMerge


T = TypeVar("T", bound="CuratorReport")


@_attrs_define
class CuratorReport:
    """One deterministic curator pass (no LLM). Lists hold fact ids.

    Attributes:
        dry_run (bool):
        ledger_path (str):
        archived (list[int] | Unset): Archived as unused past archive_after_days
        contradictions (list[CuratorContradiction] | Unset): Pending contradiction flags (both facts still active)
        duplicates_merged (list[CuratorMerge] | Unset):
        report_path (None | str | Unset):
        scanned (int | Unset):  Default: 0.
        skipped (None | str | Unset): Why the pass did not run (a guard fired)
        skipped_pinned (int | Unset):  Default: 0.
        stale (list[int] | Unset): Unused past stale_after_days (reported only)
    """

    dry_run: bool
    ledger_path: str
    archived: list[int] | Unset = UNSET
    contradictions: list[CuratorContradiction] | Unset = UNSET
    duplicates_merged: list[CuratorMerge] | Unset = UNSET
    report_path: None | str | Unset = UNSET
    scanned: int | Unset = 0
    skipped: None | str | Unset = UNSET
    skipped_pinned: int | Unset = 0
    stale: list[int] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        dry_run = self.dry_run

        ledger_path = self.ledger_path

        archived: list[int] | Unset = UNSET
        if not isinstance(self.archived, Unset):
            archived = self.archived

        contradictions: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.contradictions, Unset):
            contradictions = []
            for contradictions_item_data in self.contradictions:
                contradictions_item = contradictions_item_data.to_dict()
                contradictions.append(contradictions_item)

        duplicates_merged: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.duplicates_merged, Unset):
            duplicates_merged = []
            for duplicates_merged_item_data in self.duplicates_merged:
                duplicates_merged_item = duplicates_merged_item_data.to_dict()
                duplicates_merged.append(duplicates_merged_item)

        report_path: None | str | Unset
        if isinstance(self.report_path, Unset):
            report_path = UNSET
        else:
            report_path = self.report_path

        scanned = self.scanned

        skipped: None | str | Unset
        if isinstance(self.skipped, Unset):
            skipped = UNSET
        else:
            skipped = self.skipped

        skipped_pinned = self.skipped_pinned

        stale: list[int] | Unset = UNSET
        if not isinstance(self.stale, Unset):
            stale = self.stale

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "dry_run": dry_run,
                "ledger_path": ledger_path,
            }
        )
        if archived is not UNSET:
            field_dict["archived"] = archived
        if contradictions is not UNSET:
            field_dict["contradictions"] = contradictions
        if duplicates_merged is not UNSET:
            field_dict["duplicates_merged"] = duplicates_merged
        if report_path is not UNSET:
            field_dict["report_path"] = report_path
        if scanned is not UNSET:
            field_dict["scanned"] = scanned
        if skipped is not UNSET:
            field_dict["skipped"] = skipped
        if skipped_pinned is not UNSET:
            field_dict["skipped_pinned"] = skipped_pinned
        if stale is not UNSET:
            field_dict["stale"] = stale

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.curator_contradiction import CuratorContradiction  # noqa: PLC0415
        from ..models.curator_merge import CuratorMerge  # noqa: PLC0415

        d = dict(src_dict)
        dry_run = d.pop("dry_run")

        ledger_path = d.pop("ledger_path")

        archived = cast(list[int], d.pop("archived", UNSET))

        _contradictions = d.pop("contradictions", UNSET)
        contradictions: list[CuratorContradiction] | Unset = UNSET
        if _contradictions is not UNSET:
            contradictions = []
            for contradictions_item_data in _contradictions:
                contradictions_item = CuratorContradiction.from_dict(contradictions_item_data)

                contradictions.append(contradictions_item)

        _duplicates_merged = d.pop("duplicates_merged", UNSET)
        duplicates_merged: list[CuratorMerge] | Unset = UNSET
        if _duplicates_merged is not UNSET:
            duplicates_merged = []
            for duplicates_merged_item_data in _duplicates_merged:
                duplicates_merged_item = CuratorMerge.from_dict(duplicates_merged_item_data)

                duplicates_merged.append(duplicates_merged_item)

        def _parse_report_path(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        report_path = _parse_report_path(d.pop("report_path", UNSET))

        scanned = d.pop("scanned", UNSET)

        def _parse_skipped(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        skipped = _parse_skipped(d.pop("skipped", UNSET))

        skipped_pinned = d.pop("skipped_pinned", UNSET)

        stale = cast(list[int], d.pop("stale", UNSET))

        curator_report = cls(
            dry_run=dry_run,
            ledger_path=ledger_path,
            archived=archived,
            contradictions=contradictions,
            duplicates_merged=duplicates_merged,
            report_path=report_path,
            scanned=scanned,
            skipped=skipped,
            skipped_pinned=skipped_pinned,
            stale=stale,
        )

        curator_report.additional_properties = d
        return curator_report

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
